# 에이전트 — Chainlit 버전

UI 코드(HTML/CSS/JS)를 직접 짜지 않고 채팅 앱을 완성한 버전입니다.
Chainlit이 채팅 화면·스트리밍·세션·마크다운 렌더링·**도구 호출 과정 표시**를 전부 제공합니다.

## 무엇이 바뀌었나

- **추가**: `app_chainlit.py` — 기존 `chat_agent.py`(FastAPI + 프론트) 대체 진입점.
- **유지**: `chat_agent.py`, `templates/`, `static/` 는 그대로 둠 (언제든 롤백 가능).
- **개선**: `mcp_server.py` — 아래 백엔드 보강 적용.

### `mcp_server.py` 개선점
- 모든 `httpx` 호출에 **타임아웃**(10초) 적용 — 외부 API 지연 시 무한 대기 방지.
- `scrape_page_text`에 **SSRF 가드** — 공개 http/https URL만 허용, 사설/루프백/링크로컬(예: `169.254.169.254`) 차단. 결과 길이도 8000자로 제한.
- `get_news_headlines(limit=5)` — 전체(수십 건) 대신 **상위 N건만** 반환해 토큰 절약.
- `get_weather` — 응답 전체가 아니라 **`current_weather`만 추출**해 토큰 절약.
- `daily_quote` — `ChatOpenAI`를 **모듈 1회 생성**으로 변경(호출마다 생성 X).

## 실행 방법

```bash
# 0) 의존성
pip install -r requirements.txt

# 1) OpenAI 키
export OPENAI_API_KEY=sk-...

# 2) MCP 서버 (터미널 A) — 포트 8000
python mcp_server.py

# 3) Chainlit 앱 (터미널 B) — 포트 8000이 겹치므로 다른 포트로
chainlit run app_chainlit.py -w -p 8001
# 브라우저: http://localhost:8001
```

> 포트 메모: MCP 서버가 8000을 쓰므로 Chainlit은 `-p 8001` 등으로 분리.
> MCP 주소를 바꾸려면 `export MCP_URL=http://localhost:8000/mcp`, 모델은 `export AGENT_MODEL=...`.

## 참고
- 대화 메모리는 `InMemorySaver`

## 샘플 사진
<img width="1192" height="778" alt="스크린샷 2026-06-29 오후 7 23 26" src="https://github.com/user-attachments/assets/37191762-f75a-4767-b604-4645742e7503" />
