import hashlib
import hmac
import os
import time
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

load_dotenv()
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

SLACK_BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]
SLACK_SIGNING_SECRET = os.environ["SLACK_SIGNING_SECRET"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]

client = WebClient(token=SLACK_BOT_TOKEN)
llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.7, api_key=OPENAI_API_KEY)
# 요약은 짧게·일관되게
summarizer = ChatOpenAI(model="gpt-4o-mini", temperature=0, api_key=OPENAI_API_KEY)
app = FastAPI(title="Slack GPT Chatbot")

_seen: set[str] = set()
# 스레드별 최근 원문 메시지 + 누적 요약
_histories: dict[str, list[BaseMessage]] = defaultdict(list)
_summaries: dict[str, str] = defaultdict(str)

KEEP_RECENT = 6  # 원문으로 남길 최근 메시지 수 (대략 3턴)
SUMMARIZE_EVERY = 6  # 이 개수 넘어가면 오래된 부분을 요약에 흡수
SYSTEM_PROMPT = (
    "당신은 Slack에서 동작하는 불친절한 한국어 비서입니다. "
    "사용자 질문에 간결하고 명확하게 답하세요. "
    "이전 대화 요약이 있으면 그 맥락을 이어서 답하세요."
    "반말을 사용하세요."
    "뭐 필요하냐, 뭐가 궁금하냐 묻지 마세요. 궁금한 게 있으면 사용자가 먼저 질문 할겁니다."
)


def verify_slack_signature(body: bytes, timestamp: str | None, signature: str | None) -> None:
    if not timestamp or not signature:
        raise HTTPException(status_code=401, detail="missing signature headers")
    if abs(time.time() - int(timestamp)) > 60 * 5:
        raise HTTPException(status_code=401, detail="stale request")
    base = f"v0:{timestamp}:{body.decode('utf-8')}"
    digest = hmac.new(
        SLACK_SIGNING_SECRET.encode(),
        base.encode(),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(f"v0={digest}", signature):
        raise HTTPException(status_code=401, detail="invalid signature")


def _format_messages(messages: list[BaseMessage]) -> str:
    lines = []
    for m in messages:
        role = "사용자" if isinstance(m, HumanMessage) else "봇"
        lines.append(f"{role}: {m.content}")
    return "\n".join(lines)


def maybe_summarize(key: str) -> None:
    """최근 KEEP_RECENT개만 남기고, 더 오래된 대화는 요약에 합칩니다."""
    history = _histories[key]
    if len(history) <= SUMMARIZE_EVERY:
        return

    older = history[:-KEEP_RECENT]
    recent = history[-KEEP_RECENT:]
    prev = _summaries[key]

    prompt = [
        SystemMessage(
            content=(
                "아래 대화(와 기존 요약)를 한국어로 짧게 요약하세요. "
                "사용자 선호, 이름, 진행 중인 주제, 중요한 사실만 남기세요. "
                "3~6문장 이내로 작성하세요."
            )
        ),
        HumanMessage(
            content=(
                f"[기존 요약]\n{prev or '(없음)'}\n\n"
                f"[새로 요약할 대화]\n{_format_messages(older)}\n\n"
                "업데이트된 요약:"
            )
        ),
    ]
    result = summarizer.invoke(prompt)
    summary = result.content if isinstance(result.content, str) else str(result.content)
    _summaries[key] = summary.strip()
    _histories[key] = recent
    print("대화 요약 갱신:", _summaries[key][:120], "...")


def build_messages(key: str, user_text: str) -> list[BaseMessage]:
    history = _histories[key]
    history.append(HumanMessage(content=user_text))
    maybe_summarize(key)

    messages: list[BaseMessage] = [SystemMessage(content=SYSTEM_PROMPT)]
    summary = _summaries[key]
    if summary:
        messages.append(
            SystemMessage(content=f"이전 대화 요약:\n{summary}")
        )
    messages.extend(_histories[key])
    return messages


def reply_with_llm(channel: str, text: str, thread_ts: str | None) -> None:
    """LLM 답변을 만들고 Slack에 전송 (백그라운드). 스레드 단위 멀티턴+요약 기억."""
    key = f"{channel}:{thread_ts or 'channel'}"

    try:
        messages = build_messages(key, text)
        result = llm.invoke(messages)
        answer = result.content if isinstance(result.content, str) else str(result.content)
        _histories[key].append(AIMessage(content=answer))

        print("받은 메시지:", text)
        print("봇 답변:", answer)
        # thread_ts가 있을 때만 스레드 댓글, 없으면 채널/DM에 바로 답장
        kwargs = {"channel": channel, "text": answer}
        if thread_ts:
            kwargs["thread_ts"] = thread_ts
        client.chat_postMessage(**kwargs)
    except SlackApiError as e:
        print("postMessage 실패:", e.response.get("error"))
    except Exception as e:
        print("LLM 실패:", e)
        try:
            err_kwargs = {
                "channel": channel,
                "text": "죄송해요. 답변 생성 중 오류가 났어요.",
            }
            if thread_ts:
                err_kwargs["thread_ts"] = thread_ts
            client.chat_postMessage(**err_kwargs)
        except SlackApiError:
            pass


@app.get("/health")
def health():
    return {"ok": True, "model": "gpt-4o-mini", "memory": "summary+recent"}


@app.post("/slack/events")
async def slack_events(
    request: Request,
    background_tasks: BackgroundTasks,
    x_slack_signature: str | None = Header(default=None),
    x_slack_request_timestamp: str | None = Header(default=None),
):
    body = await request.body()
    verify_slack_signature(body, x_slack_request_timestamp, x_slack_signature)
    payload = await request.json()

    if payload.get("type") == "url_verification":
        return {"challenge": payload.get("challenge")}

    if payload.get("type") != "event_callback":
        return {"ok": True}

    event_id = payload.get("event_id")
    if event_id:
        if event_id in _seen:
            return {"ok": True}
        _seen.add(event_id)

    event = payload.get("event") or {}
    if event.get("bot_id") or event.get("subtype") == "bot_message":
        return {"ok": True}

    text = (event.get("text") or "").strip()
    channel = event.get("channel")
    if not text or not channel:
        return {"ok": True}

    if event.get("type") == "app_mention":
        parts = text.split(maxsplit=1)
        text = parts[1] if len(parts) > 1 else text

    # 사용자가 이미 스레드에서 말한 경우만 스레드에 답하고,
    # 그 외(일반 채널/DM)는 바로 메시지로 답한다.
    # 기억 키: 스레드면 스레드 기준, 아니면 채널(DM) 단위로 멀티턴 유지
    thread_ts = event.get("thread_ts")  # event["ts"]로 강제하지 않음

    background_tasks.add_task(reply_with_llm, channel, text, thread_ts)
    return {"ok": True}
