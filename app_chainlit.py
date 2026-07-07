"""Chainlit 버전의 MCP + LangGraph 에이전트
먼저 별도 터미널에서 MCP 서버를 띄운 뒤:
    python mcp_server.py            # http://localhost:8000/mcp

이 앱을 실행:
    chainlit run app_chainlit.py -w     # http://localhost:8000  (포트 충돌 시 -p 8001)

주의: 기본 포트가 MCP 서버(8000)와 겹칠 수 있으니, 겹치면
    chainlit run -w --port 8001 app_chainlit.py
처럼 포트를 다르게 주세요.
"""
from __future__ import annotations

import os

import chainlit as cl
from langchain.agents import create_agent
from langgraph.checkpoint.memory import InMemorySaver
from langchain_openai import ChatOpenAI
from langchain_mcp_adapters.client import MultiServerMCPClient

# MCP 서버 주소 (필요하면 환경변수로 덮어쓰기)
MCP_URL = os.environ.get("MCP_URL", "http://localhost:8000/mcp")
MODEL = os.environ.get("AGENT_MODEL", "gpt-5-mini")


SYSTEM_PROMPT = """당신은 친절하고 도움이 되는 AI 어시스턴트 "금토깽"입니다.

다음과 같은 도구들을 활용하여 사용자를 도와드릴 수 있습니다:
- 웹페이지의 텍스트 콘텐츠를 스크랩하여 정보를 가져올 수 있습니다
- 도시 이름을 받아 해당 도시의 현재 날씨 정보를 제공할 수 있습니다
- 구글 RSS 피드에서 최신 뉴스와 URL을 가져올 수 있습니다
- 한국 프로야구 구단의 랭킹 정보를 제공할 수 있습니다
- 일정과 스케줄 정보를 확인할 수 있습니다
- 사용자에게 영감을 주는 명언을 제공할 수 있습니다
- 사용자의 하루 일정 준비를 도와주는 브리핑 기능이 있습니다.
  사용자가 위치한 곳을 안다면 바로 brief_today() 도구의 지침을 따르면 됩니다. 아니라면, 위치를 물어보고나서 도구의 지침을 따릅니다.

사용자와의 대화에서 다음 원칙을 지켜주세요:
1. 항상 친절하고 정중한 태도로 응답해주세요
2. 사용자의 질문을 정확히 이해하고 관련된 도구를 적절히 활용해주세요
3. 최신 뉴스를 요청받으면, 도구의 출력을 그대로 출력하면 됩니다.
4. 응답은 명확하고 이해하기 쉽게 구성해주세요
5. 필요시 추가 정보나 설명을 제공하여 사용자에게 더 나은 도움을 주세요
6. 링크가 포함된 정보를 제공할 때는 [제목](URL) 형태의 마크다운 링크로 제공해주세요
"""


async def build_agent():
    """MCP 도구를 로드하고 LangGraph 에이전트를 생성합니다.

    MultiServerMCPClient가 연결 수명을 알아서 관리하므로,
    기존 코드처럼 startup에서 연 세션을 요청 태스크에서 재사용할 때 생기던
    'cancel scope in a different task' 류의 문제를 피할 수 있습니다.
    """
    client = MultiServerMCPClient(
        {
            "yozm": {
                "transport": "streamable_http",
                "url": MCP_URL,
            }
        }
    )
    tools = await client.get_tools()
    llm = ChatOpenAI(model=MODEL)
    agent = create_agent(
        model=llm,
        tools=tools,
        checkpointer=InMemorySaver(),
        system_prompt=SYSTEM_PROMPT,
    )
    return agent


@cl.on_chat_start
async def on_chat_start():
    """세션이 시작될 때 한 번 실행 — 에이전트를 만들어 세션에 보관."""
    try:
        agent = await build_agent()
    except Exception as e:  # noqa: BLE001
        await cl.Message(
            content=(
                f"⚠️ MCP 서버에 연결하지 못했습니다: `{e}`\n\n"
                f"별도 터미널에서 `python mcp_server.py`가 떠 있는지, "
                f"주소(`{MCP_URL}`)가 맞는지 확인해주세요."
            )
        ).send()
        return

    cl.user_session.set("agent", agent)
    await cl.Message(
        content="안녕하세요! 저는 금토깽이에요 🐰 날씨·뉴스·야구 순위·브리핑 등을 도와드릴게요. 무엇을 도와드릴까요?"
    ).send()


@cl.on_message
async def on_message(message: cl.Message):
    """사용자 메시지를 받아 에이전트 응답을 스트리밍합니다."""
    agent = cl.user_session.get("agent")
    if agent is None:
        await cl.Message(
            content="에이전트가 준비되지 않았습니다. MCP 서버를 켠 뒤 새 채팅을 시작해주세요."
        ).send()
        return

    # thread_id를 Chainlit 세션 id로 고정 → 대화 메모리 유지
    config = {"configurable": {"thread_id": cl.context.session.id}}
    answer = cl.Message(content="")
    tool_steps: dict[str, cl.Step] = {}

    try:
        async for event in agent.astream_events(
            {"messages": [{"role": "user", "content": message.content}]},
            config=config,
            version="v2",
        ):
            kind = event["event"]

            if kind == "on_chat_model_stream":
                chunk = event["data"]["chunk"]
                # content가 문자열이 아닐 수도 있어 방어적으로 처리
                text = getattr(chunk, "content", "") or ""
                if isinstance(text, list):
                    text = "".join(
                        part.get("text", "") if isinstance(part, dict) else str(part)
                        for part in text
                    )
                if text:
                    await answer.stream_token(text)

            elif kind == "on_tool_start":
                # 도구 호출 시작 → 접을 수 있는 Step으로 표시 (Chainlit 기본 제공)
                run_id = event.get("run_id", event["name"])
                step = cl.Step(name=event["name"], type="tool")
                step.input = event["data"].get("input")
                await step.send()
                tool_steps[run_id] = step

            elif kind == "on_tool_end":
                run_id = event.get("run_id", event["name"])
                step = tool_steps.pop(run_id, None)
                if step is not None:
                    step.output = event["data"].get("output")
                    await step.update()

    except Exception as e:  # noqa: BLE001
        await cl.Message(content=f"오류가 발생했습니다: {e}").send()
        return

    await answer.send()
