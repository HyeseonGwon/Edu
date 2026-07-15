"""
main.py  (Backend / FastAPI)
============================
LangGraph 에이전트를 HTTP API 로 노출하는 FastAPI 서버입니다.
17.3 실습("함수를 FastAPI 서버로")과 slack_app.py 의 세션 관리 패턴을 그대로 계승했습니다.

엔드포인트
----------
- GET  /health        : 서버 상태 확인
- POST /chat          : 사용자 메시지 1턴 처리 → 요구조건 수집(되묻기) 또는 검색 준비(ready)
- POST /chat/stream   : /chat 의 스트리밍(진행 문구 실시간) 버전
- POST /search/step   : 후보를 '한 개' 검색·검증 → 조건을 모두 통과하면 즉시 목록에 추가
- POST /search/finish : 검색 종료(요약 확정) — 5곳 달성/후보 소진/'그만 찾기'
- POST /reset         : 특정 세션의 대화/결과 초기화

멀티턴 유지 전략 (발표 포인트)
----------------------------
- FastAPI 는 요청 간 상태를 기억하지 않으므로, slack_app.py 처럼 메모리 세션 저장소
  (SESSIONS 딕셔너리)를 두어 세션별 대화·요구조건·결과를 보관합니다.
- 매 요청마다 '이전 대화 + 이전 요구조건'을 State 로 복원해 그래프에 넣고,
  결과를 다시 세션에 저장합니다. → 3단계 정보 수집이 여러 턴에 걸쳐 이어집니다.
"""

from __future__ import annotations

import json
import queue
import threading
import uuid

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, BaseMessage
from pydantic import BaseModel, Field

from agent.graph import trip_graph
from agent.nodes import (
    MAX_CANDIDATES_TOTAL,
    REFILL_SIZE,
    TARGET_FINALISTS,
    build_map_html,
    build_search_summary,
    reset_progress_sink,
    search_candidates,
    set_progress_sink,
    validate_place,
)
from agent.state import FamilyTripState, Place, TripRequirements

app = FastAPI(title="3세대 가족 여행 코디네이터 API")


# ──────────────────────────────────────────────────────────────────────────
# 세션 저장소 (메모리)
#  - key: session_id, value: 세션별 누적 상태
#  - 서버를 재시작하면 사라집니다(데모용). 실서비스라면 Redis/DB 로 대체.
# ──────────────────────────────────────────────────────────────────────────
class SessionData(BaseModel):
    """한 사용자(세션)의 누적 상태."""

    chat_history: list[BaseMessage] = Field(default_factory=list)
    requirements: TripRequirements | None = None
    finalists: list[Place] = Field(default_factory=list)
    map_html: str = ""

    # ── 후보를 '한 개씩' 검증하는 검색 진행용 누적 ──
    #  - search_seen     : 이미 검색으로 뽑은(=큐에 넣은) 후보 이름 (중복 검색 방지)
    #  - search_queue    : 검색으로 뽑았지만 아직 검증 안 한 후보 대기열
    #  - search_finalists: 지금까지 '조건을 모두 통과'해 확정된 최종 후보(최대 5곳)
    #  - search_tried    : 지금까지 검증한 후보 수 (무한 루프 방지 상한 판정용)
    #  - search_refill   : 후보 큐가 빌 때마다 새로 검색한 횟수 (검색어 변화용)
    search_seen: set[str] = Field(default_factory=set)
    search_queue: list[Place] = Field(default_factory=list)
    search_finalists: list[Place] = Field(default_factory=list)
    search_tried: int = 0
    search_refill: int = 0

    class Config:
        arbitrary_types_allowed = True  # BaseMessage 등 임의 타입 허용

    def reset_search(self) -> None:
        """새 검색을 시작하기 전에 이전 검색 누적을 모두 비운다."""
        self.search_seen = set()
        self.search_queue = []
        self.search_finalists = []
        self.search_tried = 0
        self.search_refill = 0
        self.finalists = []
        self.map_html = ""


SESSIONS: dict[str, SessionData] = {}


# ──────────────────────────────────────────────────────────────────────────
# 요청/응답 스키마 (FastAPI 자동 검증 — 17.2에서 배운 Pydantic 검증 철학)
# ──────────────────────────────────────────────────────────────────────────
class ChatRequest(BaseModel):
    message: str = Field(description="사용자가 이번 턴에 입력한 문장")
    session_id: str | None = Field(default=None, description="세션 식별자(없으면 서버가 새로 발급)")


class ChatResponse(BaseModel):
    session_id: str
    stage: str  # collecting / ready / searching / done / error
    assistant_message: str  # 챗봇에 표시할 답변
    map_html: str  # 결과 패널에 렌더링할 Folium 지도 HTML
    finalists: list[dict]  # 최종 후보(직렬화된 Place)
    requirements: dict | None  # 현재까지 파악된 조건


class StepRequest(BaseModel):
    """후보 '한 개' 검색·검증 요청."""

    session_id: str = Field(description="세션 식별자(조건이 저장돼 있어야 함)")
    reset: bool = Field(default=False, description="True 면 이전 검색 누적을 비우고 새로 시작")


class FinishRequest(BaseModel):
    """검색 종료(요약 확정) 요청."""

    session_id: str
    stopped: bool = Field(default=False, description="사용자가 '그만 찾기'로 멈췄는지 여부")


# ──────────────────────────────────────────────────────────────────────────
# 공용 헬퍼: 그래프 결과(dict)를 세션에 반영하고 응답 payload(dict)로 변환
#  - /chat 과 /chat/stream 이 똑같은 로직을 쓰도록 한 곳에 모았습니다.
# ──────────────────────────────────────────────────────────────────────────
def _finalize(session: SessionData, result: dict, session_id: str) -> dict:
    """LangGraph 최종 State(result)를 세션에 저장하고 응답 dict 를 만든다."""
    session.chat_history = result.get("chat_history", session.chat_history)
    session.requirements = result.get("requirements", session.requirements)
    # 정보가 모두 모여 검색 준비(ready)가 되면 이전 결과/검색 누적을 비워
    #  프런트가 /search/step 으로 '처음부터' 한 개씩 채워 나가도록 합니다.
    if result.get("stage") == "ready":
        session.reset_search()
    return {
        "session_id": session_id,
        "stage": result.get("stage", "collecting"),
        "assistant_message": result.get("assistant_message", "") or "무엇을 도와드릴까요?",
        "map_html": session.map_html,
        "finalists": [p.model_dump() if isinstance(p, Place) else p for p in session.finalists],
        "requirements": (session.requirements.model_dump() if session.requirements else None),
    }


def _error_payload(session: SessionData, session_id: str) -> dict:
    """예외 발생 시 부드럽게 돌려줄 응답 payload."""
    return {
        "session_id": session_id,
        "stage": "error",
        "assistant_message": "죄송해요, 처리 중 문제가 생겼어요. 조건을 조금 바꿔 다시 말씀해 주시겠어요?",
        "map_html": session.map_html,
        "finalists": [p.model_dump() if isinstance(p, Place) else p for p in session.finalists],
        "requirements": (session.requirements.model_dump() if session.requirements else None),
    }


# ──────────────────────────────────────────────────────────────────────────
# 엔드포인트
# ──────────────────────────────────────────────────────────────────────────
@app.get("/health")
def health():
    """서버가 살아있는지 확인하는 헬스체크."""
    return {"ok": True, "sessions": len(SESSIONS)}


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    """사용자 메시지 한 턴을 받아 LangGraph 에이전트를 실행하고 결과를 반환한다.

    처리 순서:
        1) 세션 복원(없으면 새로 발급)
        2) 이전 대화 + 요구조건으로 State 구성 후 그래프 invoke
        3) 결과를 세션에 저장하고 응답 조립
    ※ 전체를 try/except 로 감싸 어떤 예외도 500 대신 안내 문구로 반환(시연 안정성).
    """
    # 1) 세션 확보
    session_id = req.session_id or str(uuid.uuid4())
    session = SESSIONS.get(session_id) or SessionData()

    try:
        # 2) 이전 상태를 복원해 그래프 입력 State 구성
        init_state = FamilyTripState(
            chat_history=session.chat_history,   # 지금까지의 멀티턴 대화
            user_input=req.message,              # 이번 턴 입력
            requirements=session.requirements,  # 지금까지 파악된 조건(있으면)
        )

        # LangGraph 실행 — 정보가 부족하면 되물음(collecting),
        # 3가지가 모두 모이면 검색 준비 완료(ready) 신호를 돌려줍니다.
        result = trip_graph.invoke(init_state)

        # 3) 결과를 세션에 저장하고 응답 조립
        payload = _finalize(session, result, session_id)
        SESSIONS[session_id] = session
        return ChatResponse(**payload)

    except Exception as e:
        # 어떤 예외든 앱이 죽지 않도록 부드럽게 안내
        print("[/chat] 처리 중 오류:", e)
        SESSIONS[session_id] = session
        return ChatResponse(**_error_payload(session, session_id))


@app.post("/chat/stream")
def chat_stream(req: ChatRequest):
    """스트리밍 버전 /chat — 진행 상황(로딩 문구)을 실시간으로 흘려보낸다.

    응답은 'application/x-ndjson' (줄 단위 JSON) 형식으로, 각 줄은 아래 둘 중 하나:
        {"type": "progress", "message": "...지금 조회 중인 내용..."}
        {"type": "final", ...ChatResponse 와 동일한 필드...}

    구현 핵심:
        - trip_graph.stream(..., stream_mode=["custom","values"]) 로 그래프를 '흘리며' 실행
        - custom 이벤트 = 노드 안 _emit() 이 보낸 진행 문구 → 즉시 프런트로 전달
        - values 이벤트 = 매 단계의 전체 State → 마지막 것을 최종 결과로 사용
    """
    session_id = req.session_id or str(uuid.uuid4())
    session = SESSIONS.get(session_id) or SessionData()

    def event_gen():
        try:
            init_state = FamilyTripState(
                chat_history=session.chat_history,
                user_input=req.message,
                requirements=session.requirements,
            )

            final_values: dict | None = None
            # 그래프를 스트리밍 모드로 실행하며 진행 문구를 즉시 내보냄
            #  ※ 지역변수명 stream_mode 와 헷갈리지 않도록 sm 으로 받습니다.
            for sm, data in trip_graph.stream(init_state, stream_mode=["custom", "values"]):
                if sm == "custom":
                    msg = data.get("message", "") if isinstance(data, dict) else str(data)
                    if msg:
                        yield json.dumps({"type": "progress", "message": msg}, ensure_ascii=False) + "\n"
                elif sm == "values":
                    final_values = data  # 최신 전체 State (마지막 것이 최종 결과)

            # 스트림 종료 → 최종 상태로 세션 갱신 후 final 이벤트 전송
            payload = _finalize(session, final_values or {}, session_id)
            SESSIONS[session_id] = session
            yield json.dumps({"type": "final", **payload}, ensure_ascii=False) + "\n"

        except Exception as e:
            print("[/chat/stream] 처리 중 오류:", e)
            SESSIONS[session_id] = session
            yield json.dumps(
                {"type": "final", **_error_payload(session, session_id)}, ensure_ascii=False
            ) + "\n"

    return StreamingResponse(event_gen(), media_type="application/x-ndjson")


# ──────────────────────────────────────────────────────────────────────────
# 후보 '한 개' 검색·검증 엔드포인트 (핵심)
#  - 한 번 호출마다 후보 '한 개'만 검증하고, 조건을 모두 통과하면 즉시 목록에 추가합니다.
#  - 후보 대기열(search_queue)이 비면 새로 검색해 REFILL_SIZE 개를 채웁니다.
#  - 프런트가 이 엔드포인트를 반복 호출하며, 누적 5곳이 차거나(done),
#    후보가 소진되거나(exhausted), '그만 찾기' 하면 멈춥니다.
# ──────────────────────────────────────────────────────────────────────────
@app.post("/search/step")
def search_step(req: StepRequest):
    """후보 한 개를 검색·검증한다. 조건을 모두 통과하면 세션 목록에 바로 추가.

    응답(application/x-ndjson):
        {"type":"progress","message":"..."}   ← 진행 문구(실시간)
        {"type":"final", ...결과요약...}       ← 이번 스텝 종료 시 1회
    """
    session = SESSIONS.get(req.session_id)

    def _final_payload(**extra) -> str:
        base = {
            "type": "final",
            "session_id": req.session_id,
            "finalists": [p.model_dump() for p in session.search_finalists] if session else [],
            "count": len(session.search_finalists) if session else 0,
            "map_html": session.map_html if session else "",
        }
        base.update(extra)
        return json.dumps(base, ensure_ascii=False) + "\n"

    def gen():
        # 조건이 없으면(=정보 수집이 안 끝났으면) 검색 불가 → 안내 후 종료
        if session is None or session.requirements is None:
            yield json.dumps(
                {
                    "type": "final",
                    "session_id": req.session_id,
                    "finalists": [],
                    "count": 0,
                    "map_html": "",
                    "checked_name": "",
                    "checked_passed": False,
                    "added": False,
                    "done": True,
                    "exhausted": True,
                    "reached_max": True,
                    "error": "no_requirements",
                },
                ensure_ascii=False,
            ) + "\n"
            return

        requirements = session.requirements

        # 새 검색 시작이면 이전 누적을 초기화
        if req.reset:
            session.reset_search()

        # 이미 목표(5곳)를 채웠으면 더 볼 필요 없음
        if len(session.search_finalists) >= TARGET_FINALISTS:
            yield _final_payload(
                checked_name="", checked_passed=False, added=False,
                done=True, exhausted=False, reached_max=False,
            )
            return

        # 안전 상한을 넘겼으면 중단
        if session.search_tried >= MAX_CANDIDATES_TOTAL:
            yield _final_payload(
                checked_name="", checked_passed=False, added=False,
                done=False, exhausted=True, reached_max=True,
            )
            return

        # 검색/검증은 별도 스레드에서 돌리고, 진행 문구는 큐로 받아 실시간 전송합니다.
        #  (블로킹 LLM/검색 호출 중에도 progress 를 계속 흘려보내기 위함)
        q: "queue.Queue[tuple[str, object]]" = queue.Queue()
        holder: dict = {"place": None}

        def worker():
            token = set_progress_sink(lambda m: q.put(("progress", m)))
            try:
                # 1) 큐가 비면 새로 검색해 후보를 리필
                if not session.search_queue:
                    q.put(("progress", "새로운 후보 목록을 찾아보는 중이에요..."))
                    new_cands = search_candidates(
                        requirements,
                        target_count=REFILL_SIZE,
                        exclude_names=session.search_seen,
                        refill_index=session.search_refill,
                    )
                    session.search_refill += 1
                    for c in new_cands:
                        session.search_seen.add(c.name)
                    session.search_queue.extend(new_cands)

                # 2) 그래도 후보가 없으면 소진 → 검증할 것 없음
                if not session.search_queue:
                    holder["place"] = None
                    return

                # 3) 후보 한 개를 꺼내 검증 (진행 라벨: 지금까지 확정한 곳 수 표시)
                place = session.search_queue.pop(0)
                session.search_tried += 1
                label = f"(확정 {len(session.search_finalists)}/{TARGET_FINALISTS}) "
                validate_place(place, requirements, label=label)
                holder["place"] = place
            except Exception as e:  # 스텝 실패해도 앱은 계속
                print("[/search/step] worker 오류:", e)
                holder["place"] = None
            finally:
                reset_progress_sink(token)
                q.put(("done", None))

        t = threading.Thread(target=worker, daemon=True)
        t.start()

        # 워커가 끝날 때까지 진행 문구를 그대로 흘려보냄
        while True:
            kind, payload = q.get()
            if kind == "progress":
                yield json.dumps({"type": "progress", "message": payload}, ensure_ascii=False) + "\n"
            else:  # "done"
                break
        t.join()

        place: Place | None = holder.get("place")

        # 후보를 못 얻었으면(리필 실패) → 소진 처리
        if place is None:
            SESSIONS[req.session_id] = session
            yield _final_payload(
                checked_name="", checked_passed=False, added=False,
                done=len(session.search_finalists) >= TARGET_FINALISTS,
                exhausted=True,
                reached_max=session.search_tried >= MAX_CANDIDATES_TOTAL,
            )
            return

        # 조건을 '모두' 통과했으면 즉시 최종 목록에 추가 (이름 중복 방지)
        added = False
        if place.passed:
            existing = {p.name for p in session.search_finalists}
            if place.name not in existing:
                session.search_finalists.append(place)
                session.search_finalists = session.search_finalists[:TARGET_FINALISTS]
                added = True
                # 새로 추가됐을 때만 지도를 다시 그림 (기존 핀 좌표는 캐시 재사용)
                session.map_html = build_map_html(requirements, session.search_finalists)

        # 세션의 대표 결과(finalists)도 실시간 동기화 (다른 응답에서 그대로 사용)
        session.finalists = list(session.search_finalists)

        done = len(session.search_finalists) >= TARGET_FINALISTS
        reached_max = session.search_tried >= MAX_CANDIDATES_TOTAL
        SESSIONS[req.session_id] = session

        yield _final_payload(
            checked_name=place.name,
            checked_passed=bool(place.passed),
            added=added,
            done=done,
            exhausted=False,
            reached_max=reached_max,
        )

    return StreamingResponse(gen(), media_type="application/x-ndjson")


@app.post("/search/finish")
def search_finish(req: FinishRequest):
    """검색을 마무리한다: 누적 최종 후보로 안내문을 만들어 대화에 남긴다.

    - 자연 종료(5곳 채움/후보 소진)든 '그만 찾기'든 프런트가 루프를 끝낼 때 1회 호출.
    - 여기서 assistant_message 를 만들어 세션 대화에 기록해 멀티턴 연속성을 유지합니다.
    """
    session = SESSIONS.get(req.session_id)
    if session is None:
        return {"assistant_message": "세션을 찾을 수 없어요. 다시 시작해 주세요.", "finalists": [], "map_html": ""}

    finalists = session.search_finalists
    done = len(finalists) >= TARGET_FINALISTS
    summary = build_search_summary(
        session.requirements, finalists, done=done, exhausted=False, stopped=req.stopped
    )
    # 최종 확정 후보를 세션의 대표 결과로 승격 + 대화 기록
    session.finalists = list(finalists)
    session.chat_history = list(session.chat_history) + [AIMessage(content=summary)]
    SESSIONS[req.session_id] = session

    return {
        "session_id": req.session_id,
        "assistant_message": summary,
        "finalists": [p.model_dump() for p in finalists],
        "count": len(finalists),
        "map_html": session.map_html,
    }


@app.post("/reset")
def reset(req: ChatRequest):
    """세션의 대화/결과를 초기화한다. (새 여행 계획을 시작할 때)"""
    if req.session_id and req.session_id in SESSIONS:
        del SESSIONS[req.session_id]
    return {"ok": True}
