import ipaddress
import json
import socket
from datetime import datetime
from urllib.parse import urlparse

import feedparser
import httpx
from bs4 import BeautifulSoup
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from mcp.server.fastmcp import FastMCP
from geopy.geocoders import Nominatim

# ① MCP 서버 인스턴스 생성
mcp = FastMCP("simmy-ai-agent")

# 모든 외부 HTTP 호출 공통 타임아웃 (연결/읽기 모두 제한)
HTTP_TIMEOUT = httpx.Timeout(10.0, connect=5.0)
_quote_model = ChatOpenAI(model="gpt-5-mini")


def _is_public_url(url: str) -> bool:
    """http/https 스킴이고, 해석된 IP가 사설/루프백/링크로컬이 아니면 True."""
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        host = parsed.hostname
        if not host:
            return False
        # 호스트명을 실제 IP로 해석한 뒤 대역을 검사
        infos = socket.getaddrinfo(host, None)
        for info in infos:
            ip = ipaddress.ip_address(info[4][0])
            if (
                ip.is_private
                or ip.is_loopback
                or ip.is_link_local
                or ip.is_reserved
                or ip.is_multicast
            ):
                return False
        return True
    except Exception:
        return False


# ② 웹페이지 스크래핑 도구
@mcp.tool()
def scrape_page_text(url: str) -> str:
    """웹페이지의 텍스트 콘텐츠를 스크랩합니다."""
    if not _is_public_url(url):
        return f"보안상 허용되지 않는 URL입니다: {url} (공개 http/https 주소만 가능)"
    try:
        resp = httpx.get(url, timeout=HTTP_TIMEOUT, follow_redirects=True)
    except httpx.HTTPError as e:
        return f"요청 실패: {e}"

    if resp.status_code != 200:
        return f"Failed to fetch {url} (status {resp.status_code})"
    soup = BeautifulSoup(resp.text, "html.parser")
    if soup.body:
        text = soup.body.get_text(separator=" ", strip=True)
        cleaned = " ".join(text.split())  # 연속된 공백 제거
        # 너무 긴 페이지는 토큰 폭증을 막기 위해 앞부분만
        return cleaned[:8000]
    return ""


# ③ 도시명을 좌표로 변환하는 헬퍼 함수
def get_coordinates(city_name: str) -> tuple[float, float]:
    """도시 이름을 받아 위도와 경도를 반환합니다."""
    geolocator = Nominatim(user_agent="weather_app_langgraph", timeout=5)
    location = geolocator.geocode(city_name)
    if location:
        return location.latitude, location.longitude
    raise ValueError(f"좌표를 찾을 수 없습니다: {city_name}")


@mcp.tool()
def get_weather(city_name: str) -> str:
    """도시 이름을 받아 해당 도시의 현재 날씨 정보를 반환합니다."""
    print(f"날씨 조회: {city_name}")
    try:
        latitude, longitude = get_coordinates(city_name)
    except ValueError as e:
        return str(e)

    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={latitude}&longitude={longitude}&current_weather=true"
    )
    try:
        response = httpx.get(url, timeout=HTTP_TIMEOUT)
        result = response.json()
    except httpx.HTTPError as e:
        return f"날씨 정보를 가져오지 못했습니다: {e}"

    # 응답 전체가 아니라 필요한 부분(current_weather)만 추려 토큰 절약
    current = result.get("current_weather", result)
    payload = {
        "city": city_name,
        "latitude": latitude,
        "longitude": longitude,
        "current_weather": current,
    }
    print(payload)
    return json.dumps(payload, ensure_ascii=False)


# ④ 구글 뉴스 헤드라인 수집 도구
@mcp.tool()
def get_news_headlines(limit: int = 5) -> str:
    """구글 RSS피드에서 최신 뉴스와 URL을 반환합니다. (기본 상위 5건)"""
    rss_url = "https://news.google.com/rss?hl=ko&gl=KR&ceid=KR:ko"
    feed = feedparser.parse(rss_url)

    if not feed.entries:
        return "뉴스를 가져올 수 없습니다."

    # 전체(수십~100건) 대신 상위 N건만 → 프롬프트 토큰 절약
    limit = max(1, min(limit, 20))
    news_list = []
    for i, entry in enumerate(feed.entries[:limit], 1):
        title = getattr(entry, "title", "제목 없음") or "제목 없음"
        link = getattr(entry, "link", "#") or "#"
        if title == "None":
            title = "제목 없음"
        if link == "None":
            link = "#"
        news_list.append(f"{i}. [{title}]({link})")

    return "\n".join(news_list)


# ⑤  야구 순위 조회 도구
@mcp.tool()
def get_kbo_rank() -> str:
    """한국 프로야구 구단의 랭킹을 가져옵니다"""
    # 시즌을 하드코딩하지 않고 현재 연도를 사용 (지난 시즌이 나오는 문제 방지)
    season = datetime.now().year
    try:
        result = httpx.get(
            "https://sports.daum.net/prx/hermes/api/team/rank.json",
            params={"leagueCode": "kbo", "seasonKey": str(season)},
            timeout=HTTP_TIMEOUT,
        )
        return result.text
    except httpx.HTTPError as e:
        return f"KBO 순위를 가져오지 못했습니다: {e}"


# ⑥ 하드코딩된 일정 반환 도구
@mcp.tool()
def today_schedule() -> str:
    """임의의 스케줄을 반환합니다."""
    events = ["10:00 팀 미팅", "13:00 점심 약속", "15:00 프로젝트 회의", "19:00 헬스장"]
    return " | ".join(events)


# ⑦ LLM을 활용한 명언 생성 도구
@mcp.tool()
def daily_quote() -> str:
    """사용자에게 영감을 주는 명언을 출력합니다"""
    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "당신은 오늘 하루의 명언을 알려주는 도우미입니다. 사용자의 명언 요청이 있을시 명언만 출력합니다.",
            ),
            ("human", "오늘의 명언을 출력해주세요. "),
        ]
    )
    chain = prompt | _quote_model
    response = chain.invoke({})
    return response.content


# ⑧ 종합 브리핑 도구 (다른 도구들을 순차적으로 호출)
@mcp.tool()
def brief_today() -> str:
    """사용자의 하루 시작을 돕기 위해 날씨, 뉴스, 일정 등을 종합하여 전달합니다."""
    return """
다음을 순서대로 실행하고, 실행한 결과를 사용자에게 알려주세요.
첫째로 사용자가 위치한 도시를 파악하세요. 위치를 모른다면, 사용자에게 질문하세요.
둘째로 사용자의 위치를 기반으로 get_weather 도구를 호출하여 날씨 정보를 찾아서 제공합니다.
셋째로 get_news_headlines 도구를 사용하여 오늘의 주요 뉴스를 출력합니다.
넷째로 today_schedule 도구를 사용하여 오늘 사용자의 일정을 알려줍니다.
마지막으로 daily_quote 을 사용하여 명언을 출력하고, 따뜻한 말한마디를 덧붙입니다.

출력은 다음과 같이 해주세요.
## 사용자님을 위한 맞춤 요약

### 오늘의 날씨
[get_weather 의 결과]

### 오늘자 주요 뉴스
[get_news_headlines 의 결과] (링크를 함께 제공합니다)

### 오늘의 업무 일정
[today_schedule 의 결과]

### 영감을 주는 격언 한마디
[daily_quote 의 결과]
"""

if __name__ == "__main__":
    # MCP 서버 실행 (HTTP 스트리밍 모드, 포트 8000)
    mcp.run(transport="streamable-http")
