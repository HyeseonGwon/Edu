import hashlib
import hmac
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Request
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

load_dotenv()
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

SLACK_BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]
SLACK_SIGNING_SECRET = os.environ["SLACK_SIGNING_SECRET"]

client = WebClient(token=SLACK_BOT_TOKEN)
app = FastAPI(title="Slack Echo Bot")
_seen: set[str] = set()


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


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/slack/events")
async def slack_events(
    request: Request,
    x_slack_signature: str | None = Header(default=None),
    x_slack_request_timestamp: str | None = Header(default=None),
):
    body = await request.body()
    verify_slack_signature(body, x_slack_request_timestamp, x_slack_signature)
    payload = await request.json()

    # Slack Event Subscriptions URL 검증
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
    # 봇 메시지는 무시 (안 그러면 자기 echo에 또 반응)
    if event.get("bot_id") or event.get("subtype") == "bot_message":
        return {"ok": True}

    text = (event.get("text") or "").strip()
    channel = event.get("channel")
    if not text or not channel:
        return {"ok": True}

    # app_mention 이면 "<@U...> 내용" 에서 내용만 사용
    if event.get("type") == "app_mention":
        parts = text.split(maxsplit=1)
        text = parts[1] if len(parts) > 1 else text

    echo = f"[echo] {text}"
    print("받은 메시지:", text)
    try:
        # thread_ts를 넣지 않으면 채널에 일반 메시지로 올라갑니다
        client.chat_postMessage(channel=channel, text=echo)
    except SlackApiError as e:
        print("postMessage 실패:", e.response.get("error"))

    return {"ok": True}
