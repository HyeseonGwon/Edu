"""
nodes.py
========
가족 여행 코디네이터의 '두뇌'를 이루는 함수들을 정의합니다.

⚠️ 구조 주의: 4개가 모두 LangGraph 노드로 순차 실행되는 게 아닙니다.
   - LangGraph 그래프에 등록된 노드는 (1) state_manager_node '하나'뿐입니다.
     (graph.py: START → state_manager → END)
   - 나머지(후보 발굴·검증·지도)는 그래프 노드가 아니라, FastAPI 의 /search/step 이
     '후보를 한 개씩' 반복 호출하는 '헬퍼 함수'입니다.
   - 이렇게 그래프 밖에서 한 개씩 처리하는 이유: 결과를 실시간으로 하나씩 보여주고,
     사용자가 언제든 '그만 찾기'로 멈출 수 있게 하기 위함입니다.
     (graph.invoke 는 원자적 실행이라 중간에 부분 결과를 흘리며 멈추기 어렵습니다.)

    [1] 대화 단계 — LangGraph 그래프
        START ─► state_manager_node ─► END
                  │  정보 부족  → 되물음(stage='collecting')  → 사용자 답변 대기
                  └  정보 충족  → 검색 준비(stage='ready')

    [2] 검색 단계 — FastAPI /search/step 가 후보 1개씩 반복 (그래프 밖)
        search_candidates   후보 발굴: 반경 기반 쿼리 확장 + (Kakao 좌표) 지리 게이트
             │
             ▼  (후보 큐에서 한 곳)
        validate_place      ★핵심★ 계단/아이메뉴 조건 '심층 검증' → 통과한 곳만 채택
             │
             ▼
        build_map_html / build_search_summary   지도(Folium)·요약 갱신
        └ 통과 시 즉시 리스트에 추가. TARGET_FINALISTS(5) 달성/후보 소진/'그만 찾기'까지 반복.

공통 설계 원칙 (발표 포인트)
--------------------------
- state_manager_node 는 15.1 실습대로 `state: FamilyTripState` 를 받아 `dict` 를 반환합니다.
- 검색/파싱 실패에도 멈추지 않도록 방어적으로 처리해, "정보를 찾기 어렵습니다" 식으로
  부드럽게 넘어갑니다. (실시간 시연 안정성)
- LLM에게 자유 서술 대신 `with_structured_output` 으로 '구조화된 답'을 강제해
  (15.4 실습 패턴) 후속 코드가 안정적으로 값을 꺼내 쓸 수 있게 합니다.
"""

from __future__ import annotations

import contextvars
import os
from typing import Callable, Literal, Optional

import folium
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from .config import make_llm
from .state import FamilyTripState, Place, TripRequirements
from .tools import geocode_place, locate_place, targeted_review_search, web_search_raw

# LangGraph 커스텀 스트리밍용 writer (없는 버전이어도 앱이 죽지 않게 방어적 import)
try:
    from langgraph.config import get_stream_writer
except Exception:  # pragma: no cover
    get_stream_writer = None


# ──────────────────────────────────────────────────────────────────────────
# 진행 문구(로딩) 전달 경로
#  1) LangGraph 스트림 컨텍스트: get_stream_writer() 로 그래프 스트림에 실어 보냄
#  2) 수동 sink: 그래프 밖(예: /search/step 후보 검증)에서 진행 문구를 받아야 할 때 사용.
#     - contextvar 로 두어 '해당 스레드'에서만 유효하게 합니다.
# ──────────────────────────────────────────────────────────────────────────
_progress_sink: contextvars.ContextVar[Optional[Callable[[str], None]]] = contextvars.ContextVar(
    "progress_sink", default=None
)


def set_progress_sink(sink: Optional[Callable[[str], None]]):
    """현재 컨텍스트(스레드)의 진행 문구 수신 함수를 지정한다. (되돌릴 token 반환)"""
    return _progress_sink.set(sink)


def reset_progress_sink(token) -> None:
    """set_progress_sink 가 준 token 으로 이전 상태를 복원한다."""
    try:
        _progress_sink.reset(token)
    except Exception:
        pass


def _emit(message: str) -> None:
    """진행 상황(로딩 문구)을 실시간으로 내보낸다.

    - /chat/stream(그래프 스트림): writer 로 흘려보내 "지금 무엇을 조회 중"인지 실시간 표시.
    - /search/step(후보 검증): 수동 sink 로 전달돼 동일하게 실시간 표시.
    - 그 외(CLI 등): 콘솔에만 출력.
    ※ 어떤 상황에서도 예외를 던지지 않습니다. (시연 안정성)
    """
    print("[진행]", message)  # 콘솔 로그 (서버 터미널에서도 진행상황 확인 가능)

    # 1) LangGraph 스트림 컨텍스트 우선
    if get_stream_writer is not None:
        try:
            writer = get_stream_writer()
            if writer is not None:
                writer({"message": message})
                return
        except Exception:
            pass  # 스트리밍 컨텍스트가 아니면 아래 sink 로 폴백

    # 2) 수동 sink (배치 검색 등)
    sink = _progress_sink.get()
    if sink is not None:
        try:
            sink(message)
        except Exception:
            pass

# ──────────────────────────────────────────────────────────────────────────
# 튜닝 상수 (단일 방식: 후보를 '한 개씩' 검증해 조건을 모두 통과하면 즉시 리스트에 추가)
#  - 조건을 모두 통과한 곳을 TARGET_FINALISTS(5곳) 채우거나,
#    후보가 소진되거나, 사용자가 '그만 찾기'를 누를 때까지 반복합니다.
# ──────────────────────────────────────────────────────────────────────────
TARGET_FINALISTS = 5      # 목표 최종 후보 수(이만큼 채우면 자동 종료)
REFILL_SIZE = 10          # 후보 큐가 비면 한 번에 새로 검색해 채울 후보 이름 수
MAX_CANDIDATES_TOTAL = 40  # 무한 루프 방지: 최대 이만큼 후보를 조사하면 중단
SEARCH_RADIUS_KM = 5.0    # 지도 '검색 영역' 원의 반경(km). 이 반경 안에서 확실히 찾은 곳만 핀 표시


# ──────────────────────────────────────────────────────────────────────────
# LLM 인스턴스 (역할별로 온도를 다르게)
#  - extractor/validator: 온도 0 → 사실 추출·판정은 일관되게
#  - writer: 온도 0.4 → 사용자에게 보여줄 안내문은 살짝 자연스럽게
# ──────────────────────────────────────────────────────────────────────────
extractor_llm = make_llm(temperature=0.0)
validator_llm = make_llm(temperature=0.0)
writer_llm = make_llm(temperature=0.4)


# ══════════════════════════════════════════════════════════════════════════
# [그래프 노드] State Manager (상태 관리자) — 이 파일에서 유일한 LangGraph 노드
#  - 대화에서 3단계(누구와/무엇을/어디로)를 파악해 구조화(JSON)합니다.
#  - 부족하면 되물을 질문을 만들고, 다 모이면 stage='ready' 로 검색 준비를 알립니다.
# ══════════════════════════════════════════════════════════════════════════
class CollectResult(BaseModel):
    """State Manager 가 LLM으로부터 받아낼 '구조화된 대화 분석 결과'.

    지금까지의 대화 전체를 근거로, 아래 필드를 '현재까지 파악된 상태'로 채웁니다.
    아직 모르는 값은 빈 문자열/False 로 둡니다.
    """

    companions: str = Field(default="", description="누구와 함께 가는지 (사용자가 말한 경우만, 아니면 빈 문자열)")
    place_type: str = Field(
        default="",
        description=(
            "무엇을 찾는지 (식당/숙소/둘다). 사용자가 명시했거나 문맥상 분명히 추론되면 채웁니다. "
            "예: '배고프다/밥/맛집/식사' → '식당', '떠나고싶다/쉬고싶다/자고싶다/1박' → '숙소'. "
            "근거가 전혀 없으면 빈 문자열."
        ),
    )
    region: str = Field(
        default="",
        description=(
            "어디로 = 검색 지역. 사용자가 '실제 지명'을 직접 말했거나 어시스턴트의 지역 제안을 긍정한 경우에만 채웁니다. "
            "추측하거나 '서울' 같은 기본값을 넣지 말고, 없으면 반드시 빈 문자열로 두세요."
        ),
    )
    region_guess: str = Field(
        default="",
        description=(
            "사용자가 특정 랜드마크·명소를 말해 지역이 '추론'되면 그 지역명을 여기 적습니다"
            "(예: '롯데월드'→'서울 잠실', '에버랜드'→'경기 용인', '해운대'→'부산'). "
            "region 을 이미 채웠거나 추론 근거가 없으면 빈 문자열."
        ),
    )
    region_undecided: bool = Field(
        default=False,
        description=(
            "사용자가 지역을 정하지 못했다고 하면(예: '아무데나/어디든/모르겠어/추천해줘/어디가 좋을까') True."
        ),
    )
    need_no_stairs: bool = Field(default=False, description="계단이 적어야 하면 True")
    need_kid_friendly: bool = Field(default=False, description="어린이/안매운 메뉴가 필요하면 True")
    extra_notes: str = Field(default="", description="기타 요청")
    info_complete: bool = Field(
        description="누구와/무엇을/어디로 3가지가 모두 파악되었으면 True"
    )
    follow_up_question: str = Field(
        default="",
        description="아직 부족한 정보를 자연스럽게 되묻는 한국어 질문(완료면 빈 문자열)",
    )


# LLM에 구조화 출력을 강제 (15.4의 with_structured_output 패턴)
collector = extractor_llm.with_structured_output(CollectResult)

STATE_MANAGER_SYSTEM = (
    "당신은 3세대(조부모·부모·아이) 가족 여행을 돕는 친절한 한국어 코디네이터입니다.\n"
    "목표: 사용자와의 대화에서 '① 누구와, ② 무엇을(식당/숙소), ③ 어디로(지역)' 3가지를 파악하는 것입니다.\n"
    "\n"
    "[채우기 원칙 — 매우 중요]\n"
    "- 각 항목은 근거 없이 추측하지 마세요. 특히 임의의 기본값(예: '서울')은 절대 넣지 마세요.\n"
    "- '무엇을(place_type)'은 사용자가 식당/밥/식사/맛집/카페 또는 숙소/호텔/펜션 등을 '명시'하면 채웁니다. "
    "명시하지 않았어도 문맥상 분명히 추론되면 채우세요 "
    "(예: '배고프다/밥/맛집' → '식당', '떠나고싶다/쉬고싶다/자고싶다/1박' → '숙소').\n"
    "- '어디로(region)'는 더 엄밀합니다. 사용자가 '실제 지명'을 직접 말했을 때만 region 을 채우고, "
    "지명이 없으면 region 은 반드시 빈 문자열로 두세요('배고프다' 같은 말에는 지역이 없습니다).\n"
    "- 다만 사용자가 랜드마크·명소를 말해 지역이 추론되면, region 이 아니라 region_guess 에 그 지역을 적으세요 "
    "(예: '롯데월드'→'서울 잠실', '에버랜드'→'경기 용인', '해운대'→'부산'). region 은 여전히 빈 문자열로 둡니다.\n"
    "- 사용자가 지역을 정하지 못했다고 하면(예: '아무데나/어디든/모르겠어/추천해줘/어디가 좋을까') region_undecided=True 로 두세요.\n"
    "- 사용자가 어시스턴트의 제안(예: '잠실 맞을까요?')에 '응/네/맞아/그래' 등으로 '긍정'하면, "
    "그 값을 명시한 것으로 간주해 해당 항목(region 등)을 채우세요.\n"
    "\n"
    "규칙:\n"
    "- 지금까지의 대화 '전체'를 근거로 현재까지 파악된 값을 채우세요. 이미 '명시된' 정보는 유지하세요.\n"
    "- '할머니/할아버지/조부모/노부모/어르신/아기/유모차/휠체어/무릎/거동' 등 "
    "거동이 불편할 수 있는 동반자가 언급되면 need_no_stairs=True (계단이 적은 곳) 로 두세요.\n"
    "- '아이/아들/딸/자녀/어린이/유아/안 매운' 등 아이 동반이 언급되면 "
    "need_kid_friendly=True (맵지 않은/어린이 메뉴) 로 두세요.\n"
    "- 3가지가 모두 파악되면 info_complete=True, 아니면 False 로 두고,\n"
    "  부족한 항목 '하나만' 콕 집어 자연스럽게 되묻는 질문을 follow_up_question 에 쓰세요.\n"
    "- 되물을 때는 한 번에 하나씩만 물어 사용자가 부담을 느끼지 않게 하세요."
)


# 3세대(조부모·부모·아이) 가족 여행으로 무난한 지역 추천 목록.
#  사용자가 지역을 정하지 못했을 때 몇 곳을 제시해 '명시적 선택'을 유도한다.
REGION_SUGGESTIONS = [
    "서울 송파(잠실)",
    "서울 용산",
    "경기 고양(일산 호수공원)",
    "부산 해운대",
    "강원 강릉",
]


def _ask_region(region_guess: str, region_undecided: bool) -> str:
    """지역이 아직 없을 때 되물을 문장을 만든다.

    - 랜드마크로 추론된 지역(region_guess)이 있으면 그걸 '제안'하며 확인을 요청한다.
    - 사용자가 정하지 못했으면(region_undecided) 몇 곳을 '추천'해 명시적 선택을 유도한다.
    - 그 외에는 예시와 함께 지역을 묻는다.
    지역은 엄밀한 조건이라, 어떤 경우에도 값을 임의로 확정하지 않고 되묻기만 한다.
    """
    if region_guess:
        return (
            f"혹시 '{region_guess}' 쪽을 말씀하시는 걸까요? 맞으면 그렇다고 알려주세요. "
            "(다른 지역이면 지역명을 직접 말씀해 주세요.)"
        )
    if region_undecided:
        picks = " · ".join(REGION_SUGGESTIONS)
        return (
            "어디로 갈지 아직 정하지 못하셨군요! 3세대 가족 여행으로는 예를 들어 "
            f"{picks} 같은 곳이 좋아요. 이 중 어디가 마음에 드시나요? (다른 지역도 좋습니다.)"
        )
    return "어느 지역으로 가시나요? (예: 서울 송파구, 부산 해운대)"


def state_manager_node(state: FamilyTripState) -> dict:
    """[그래프 노드] 대화를 분석해 요구조건을 구조화하고, 다음 행동(질문 vs 검색)을 결정한다.

    반환 dict 로 State 를 갱신하며, 특히:
        - requirements       : 구조화된 조건(TripRequirements)
        - info_complete      : 검색을 시작해도 되는지 여부(라우팅 근거)
        - follow_up_question : 부족 시 사용자에게 되물을 질문
        - chat_history       : 이번 턴 사용자 메시지(+되물음)를 대화에 누적
    """
    user_text = (state.user_input or "").strip()
    _emit("입력을 이해하고 조건을 정리하는 중이에요...")

    # 이전 턴까지 파악된 조건을 LLM에게 '힌트'로 전달 (정보 유지에 도움)
    prev = state.requirements
    prev_hint = ""
    if prev is not None:
        prev_hint = (
            f"\n[지금까지 파악된 조건]\n"
            f"- 누구와: {prev.companions or '(미정)'}\n"
            f"- 무엇을: {prev.place_type or '(미정)'}\n"
            f"- 어디로: {prev.region or '(미정)'}\n"
            f"- 계단조건 필요: {prev.need_no_stairs}, 아이메뉴 필요: {prev.need_kid_friendly}\n"
        )

    try:
        # 대화 맥락 + 이번 발화 + 이전 힌트를 모아 구조화 추출
        messages = [
            SystemMessage(content=STATE_MANAGER_SYSTEM),
            *state.chat_history,  # 지금까지의 멀티턴 대화
            HumanMessage(content=f"{user_text}{prev_hint}"),
        ]
        result: CollectResult = collector.invoke(messages)
    except Exception as e:
        # LLM/파싱 실패 시: 안전하게 '아직 부족' 처리하고 되물음
        print("[state_manager] 추출 실패:", e)
        return {
            "chat_history": [HumanMessage(content=user_text)] if user_text else [],
            "info_complete": False,
            "follow_up_question": "어떤 가족 구성으로, 어느 지역에서, 식당과 숙소 중 무엇을 찾고 계신가요?",
            "assistant_message": "어떤 가족 구성으로, 어느 지역에서, 식당과 숙소 중 무엇을 찾고 계신가요?",
            "stage": "collecting",
        }

    # 추출 결과를 우리 도메인 모델(TripRequirements)로 변환
    companions = (result.companions or "").strip()
    place_type = (result.place_type or "").strip()
    region = (result.region or "").strip()
    # 지역은 엄밀히 다룬다: 명시된 지명만 region 에 채우고,
    #  랜드마크로 추론된 지역은 region_guess(제안·확인용), 미정 의사는 region_undecided(추천용) 로 받는다.
    region_guess = (result.region_guess or "").strip()
    region_undecided = bool(result.region_undecided)
    requirements = TripRequirements(
        companions=companions,
        place_type=place_type,
        region=region,
        need_no_stairs=result.need_no_stairs,
        need_kid_friendly=result.need_kid_friendly,
        extra_notes=result.extra_notes,
    )

    # ★ 완료 판정은 LLM 판단(result.info_complete)에 맡기지 않고,
    #   '누구와/무엇을/어디로' 3개 필드가 실제로 채워졌는지로 '결정적으로' 정합니다.
    #   (LLM이 음식종류 등 불필요한 정보를 더 요구해 흐름이 막히는 것을 방지 — 시연 예측 가능성↑)
    info_complete = bool(companions and place_type and region)

    # 대화 누적: 이번 턴 사용자 메시지는 항상 기록
    new_messages: list = [HumanMessage(content=user_text)] if user_text else []

    if not info_complete:
        # 정보 부족 → 부족한 '핵심 항목'을 콕 집어 되묻습니다.
        #   (딱 필요한 3가지만 순서대로 수집해 대화가 옆길로 새지 않게 함)
        if not companions:
            question = "누구와 함께 가시나요? (예: 부모님·아기와 함께 3세대)"
        elif not place_type:
            question = "식당과 숙소 중 무엇을 찾으시나요?"
        elif not region:
            # 지역은 엄밀히: 랜드마크 추론은 '제안·확인', 미정이면 '추천'으로 명시적 선택을 유도.
            question = _ask_region(region_guess, region_undecided)
        else:
            question = result.follow_up_question or "조금 더 자세히 알려주실 수 있을까요?"
        new_messages.append(AIMessage(content=question))
        return {
            "chat_history": new_messages,
            "requirements": requirements,
            "info_complete": False,
            "follow_up_question": question,
            "assistant_message": question,
            "stage": "collecting",
        }

    # 정보 충족 → 그래프는 여기서 끝(END). 실제 검색은 프런트가 /search/step 으로
    #   후보를 '한 개씩' 검증하며 진행합니다. (stage='ready' 가 그 시작 신호)
    print(f"[state_manager] 정보 충족 → 검색 준비 | {companions} / {place_type} / {region}")
    ready_msg = (
        "조건이 모두 모였어요! 지금부터 후보를 하나씩 확인해, 조건을 모두 통과한 곳을 "
        "바로바로 목록에 올릴게요. 최종 5곳을 채우거나 '그만 찾기'를 누르면 멈춰요."
    )
    new_messages.append(AIMessage(content=ready_msg))
    return {
        "chat_history": new_messages,
        "requirements": requirements,
        "info_complete": True,
        "follow_up_question": "",
        "assistant_message": ready_msg,
        "stage": "ready",
    }


# ══════════════════════════════════════════════════════════════════════════
# 후보 탐색 (Candidate Search)
#  - 웹 검색 + LLM 추출로 지역 내 식당 후보 이름을 뽑아냅니다.
#  - /search/step 엔드포인트가 후보 큐가 비면 이 함수로 새 후보를 '리필'합니다.
# ══════════════════════════════════════════════════════════════════════════
class _CandidateItem(BaseModel):
    """LLM이 검색 스니펫에서 추출할 후보 1건."""

    name: str = Field(description="가게의 '상호명'(간판 이름). 메뉴·음식 이름이 아님")
    category: str = Field(default="", description="분류 (식당/숙소/카페 등)")
    address: str = Field(default="", description="스니펫에서 확인되는 '해당 지역' 포함 주소/위치. 없으면 빈 값")
    reason: str = Field(default="", description="후보로 뽑은 한 줄 이유")
    in_region: bool = Field(
        default=False,
        description="이 가게가 사용자가 찾는 '그 지역'에 실제로 위치한다는 근거가 스니펫에 있으면 True",
    )


class CandidateList(BaseModel):
    """후보 목록 컨테이너 (구조화 출력용)."""

    places: list[_CandidateItem] = Field(default_factory=list)


candidate_extractor = extractor_llm.with_structured_output(CandidateList)


# 후보 검색에 쓸 검색어 '템플릿 풀'.
#  - 리필(refill)마다 다른 검색어를 써야 '새로운' 후보가 나오므로 넉넉히 둡니다.
QUERY_TEMPLATES = [
    "{region} 가족 {pt} 추천",
    "{region} 아이랑 갈만한 {pt}",
    "{region} {pt} 노키즈 아닌 곳 추천",
    "{region} 유모차 {pt} 추천",
    "{region} {pt} 맛집",
    "{region} 가볼만한 {pt}",
    "{region} {pt} 후기",
    "{region} 어르신 모시고 갈 {pt}",
    "{region} 룸 있는 {pt}",
    "{region} 조용한 {pt} 추천",
]

# 반경이 넓을 때 '인접 지역'까지 후보를 넓히기 위한 검색어 풀.
#  - 반경(radius_km)이 클수록 이 쿼리를 더 섞어, 지역 경계 밖 근처 동네도 후보로 끌어옵니다.
NEARBY_QUERY_TEMPLATES = [
    "{region} 근처 {pt} 추천",
    "{region} 인근 {pt} 맛집",
    "{region} 주변 가볼만한 {pt}",
    "{region} 근교 가족 {pt}",
]


def _nearby_query_count(radius_km: float) -> int:
    """반경(km)에 따라 '인접 지역' 확장 검색어를 몇 개 섞을지 정한다.

    - 좁은 반경(≤3km): 0개 → 지역에 딱 붙여 검색 (도심에서 너무 넓히지 않음)
    - 중간(4~9km): 1개
    - 넓은 반경(≥10km): 2개 → 근처 동네까지 적극적으로 확장
    """
    if radius_km <= 3:
        return 0
    if radius_km < 10:
        return 1
    return 2


def search_candidates(
    req: TripRequirements,
    target_count: int,
    exclude_names: set[str] | None = None,
    refill_index: int = 0,
    radius_km: float = SEARCH_RADIUS_KM,
) -> list[Place]:
    """검색 → LLM 추출로 후보(Place) 리스트를 만든다. (후보 큐 '리필'용)

    ★ 반경 반영 (요청 사항)
        - radius_km 이 클수록 '근처/인근/주변' 검색어를 더 섞어 인접 지역 후보까지 넓힙니다.
        - 카카오 키가 있으면 후보를 '지역 중심 반경 이내'로 실제 필터링합니다(좌표도 미리 확보).
          → 반경을 좁히면 먼 곳이 후보에서 빠지고, 넓히면 근처 동네까지 후보로 들어옵니다.
        - 카카오 키가 없으면(무료 폴백) 반경을 좌표로 강제하기 어려우므로, 기존처럼
          LLM 의 in_region 판단으로 지역을 거릅니다.

    Args:
        req: 요구조건(지역/유형 등).
        target_count: 한 번에 뽑을 후보 수.
        exclude_names: 이미 조사한 이름들(중복 제외용).
        refill_index: 리필 회차. 회차마다 다른 검색어를 골라 '새로운' 후보를 유도.
        radius_km: 검색 반경(km). 검색어 확장 강도와 지리적 필터 반경에 함께 쓰입니다.

    Returns:
        새 후보 Place 리스트(중복/기존 이름/반경 밖 제외). 검색 실패 시 빈 리스트.
    """
    exclude_names = exclude_names or set()
    place_type = req.place_type or "식당"
    region = req.region or "서울"
    radius_km = max(1.0, min(float(radius_km), 20.0))
    center = _region_center(region)
    kakao_on = bool(os.getenv("KAKAO_REST_API_KEY"))

    # 리필 회차에 따라 검색어 풀에서 다른 구간을 골라 씁니다.
    #  - 예: 0회차 → 0~3번, 1회차 → 2~5번 ... 겹치며 이동해 새 결과를 유도
    start = (refill_index * 2) % len(QUERY_TEMPLATES)
    picked = [QUERY_TEMPLATES[(start + k) % len(QUERY_TEMPLATES)] for k in range(4)]
    queries = [t.format(region=region, pt=place_type) for t in picked]

    # 반경이 넓으면 '인접 지역' 검색어를 덧붙여 후보 발굴 범위를 넓힙니다.
    n_nearby = _nearby_query_count(radius_km)
    for k in range(n_nearby):
        tmpl = NEARBY_QUERY_TEMPLATES[(refill_index + k) % len(NEARBY_QUERY_TEMPLATES)]
        queries.append(tmpl.format(region=region, pt=place_type))

    raw_snippets: list[str] = []
    for q in queries:
        for r in web_search_raw(q, max_results=5):
            raw_snippets.append(f"- {r['title']} | {r['body']} | {r['href']}")

    if not raw_snippets:
        return []

    snippet_text = "\n".join(raw_snippets)[:6000]

    # 이미 조사한 이름은 LLM에게도 '제외'하라고 알려 새 후보 위주로 뽑게 합니다.
    exclude_hint = ""
    if exclude_names:
        sample = list(exclude_names)[:30]
        exclude_hint = "\n- 아래 이미 조사한 곳은 제외하세요: " + ", ".join(sample)

    # 반경에 따라 '지역 범위' 안내를 다르게: 넓으면 인접 동네 허용, 좁으면 지역에 밀착.
    if radius_km >= 7:
        region_rule = (
            f"2. '{region}' 중심에서 대략 반경 {radius_km:.0f}km 이내면 됩니다. "
            f"'{region}' 바로 옆 인접 동네·이웃 지역도 포함하세요(in_region=True). "
            "다만 명백히 멀리 떨어진 다른 도시/여행지는 제외하세요.\n"
        )
    else:
        region_rule = (
            f"2. 반드시 '{region}' 또는 바로 인접한 곳(중심 반경 {radius_km:.0f}km 이내)만 포함하세요. "
            f"스니펫에 '{region}' 또는 그 안의 동/도로명 주소가 함께 나오면 in_region=True 로 두세요. "
            "지역이 불분명하거나 다른 지역(타 도시·캠핑장·여행지 등)으로 보이면 제외하세요.\n"
        )

    system = (
        "당신은 웹 검색 스니펫에서 '실제 존재하는 가게의 상호명'만 정확히 골라내는 정리 도우미입니다.\n"
        f"사용자는 '{region}' 중심 반경 약 {radius_km:.0f}km 이내의 '{place_type}'를 찾고 있습니다.\n\n"
        "[반드시 지켜야 할 규칙]\n"
        "1. '상호명(간판 이름)'만 추출하세요. 메뉴·음식 이름은 상호명이 아닙니다.\n"
        "   - 메뉴/음식(잘못된 예): '치즈닭갈비', '우대갈비', '마라탕', '한우오마카세', '파스타'\n"
        "   - 상호명(올바른 예): '계탄언니', '스시코우지', '할머니국수'\n"
        "   - \"○○에서 △△를 먹었다\" 형태면 ○○가 상호명, △△는 메뉴이니 △△는 절대 넣지 마세요.\n"
        + region_rule
        + "3. 블로그/뉴스 '제목'이나 홍보 문구, 리스트 글 제목을 상호명으로 착각하지 마세요.\n"
        f"4. 같은 곳은 한 번만. 이름이 불분명하면 제외. 최대 {target_count}곳.\n"
        "5. address 에는 스니펫에서 확인되는 주소/위치를 적으세요. 없으면 빈 값." + exclude_hint
    )
    try:
        extracted: CandidateList = candidate_extractor.invoke(
            [
                SystemMessage(content=system),
                HumanMessage(
                    content=(
                        f"[검색 스니펫]\n{snippet_text}\n\n"
                        f"위에서 '{region}' 중심 반경 약 {radius_km:.0f}km 이내의 '{place_type}' 상호명만 추출하세요. "
                        "메뉴·음식 이름과 명백히 먼 다른 지역 가게는 제외하세요."
                    )
                ),
            ]
        )
    except Exception as e:
        print("[search_candidates] 후보 추출 실패:", e)
        return []

    # 코드 레벨에서도 중복/기존 이름/반경 밖 후보를 한 번 더 걸러 냅니다(안전장치).
    seen_lower = {n.lower() for n in exclude_names}
    candidates: list[Place] = []
    for item in extracted.places:
        name = (item.name or "").strip()
        if not name or name.lower() in seen_lower:
            continue

        # 카카오가 없을 때만 LLM 의 in_region 판단에 의존 (좌표로 반경을 강제할 수 없으므로).
        if not kakao_on and not item.in_region:
            print(f"[search_candidates] 지역 근거 부족으로 제외: {name}")
            continue

        seen_lower.add(name.lower())  # 이번 리필 내 중복도 방지
        place = Place(
            name=name,
            category=item.category or place_type,
            address=item.address,
            reason=item.reason,
        )

        # ★ 반경 반영의 핵심: 카카오가 있으면 '중심 반경 이내'에서 실제로 찾은 곳만 후보로 채택.
        #    좌표를 여기서 확보해 두면 지도(build_map_html)가 재지오코딩 없이 그대로 핀을 찍습니다.
        if kakao_on:
            coords = locate_place(place.name, place.address, region, center, radius_km)
            if coords is None:
                print(f"[search_candidates] 반경 {radius_km:.0f}km 밖/미확인으로 제외: {name}")
                continue
            place.lat, place.lng = coords
            place.located = True
            place.geo_checked = True

        candidates.append(place)
        if len(candidates) >= target_count:
            break
    return candidates


# ══════════════════════════════════════════════════════════════════════════
# 후보 검증 (Deep Validator) ★가장 중요★ — 그래프 노드가 아니라 /search/step 이 후보별로 호출
#  - 후보 '한 개'의 계단/메뉴 조건을 타겟 검색 + LLM 판독으로 검증합니다.
#  - 불확실하거나 조건 미달이면 통과(passed=False). 모두 통과해야 최종 목록에 오릅니다.
# ══════════════════════════════════════════════════════════════════════════
class ConditionCheck(BaseModel):
    """LLM이 리뷰 스니펫을 읽고 내리는 '조건별 판정' 결과.

    ★ 정확도 개선 포인트:
      각 조건마다 '판정의 결정적 근거가 된 리뷰 번호'(*_source_index)를 함께 받아,
      그 번호에 해당하는 리뷰의 URL 만 '자세히 보기' 링크로 씁니다.
      → 판정 근거와 출처 링크가 항상 같은 글을 가리키도록 보장합니다.
      (근거가 없어 unknown 으로 판정하면 index 는 0.)
    """

    stair_status: Literal["yes", "no", "unknown"] = Field(
        description="계단 접근성. yes=계단 적음/1층/엘리베이터 있음, no=계단 많음, unknown=근거없음"
    )
    stair_note: str = Field(default="", description="계단 판정의 짧은 근거")
    stair_source_index: int = Field(
        default=0,
        description="계단 판정의 '결정적 근거'가 된 [계단 관련 리뷰]의 번호(1부터). 근거가 없으면 0.",
    )
    menu_status: Literal["yes", "no", "unknown"] = Field(
        description="어린이/안매운 메뉴. yes=있음, no=없음/전부 매움, unknown=근거없음"
    )
    menu_note: str = Field(default="", description="메뉴 판정의 짧은 근거")
    menu_source_index: int = Field(
        default=0,
        description="메뉴 판정의 '결정적 근거'가 된 [메뉴 관련 리뷰]의 번호(1부터). 근거가 없으면 0.",
    )


condition_checker = validator_llm.with_structured_output(ConditionCheck)

VALIDATOR_SYSTEM = (
    "당신은 가족 접근성 정보를 '리뷰 근거만으로' 판정하는 깐깐한 검증 에이전트입니다.\n"
    "주어진 블로그/리뷰 스니펫에 '명확한 근거'가 있을 때만 yes/no 로 판정하고,\n"
    "근거가 애매하거나 없으면 반드시 unknown 으로 판정하세요. (추측 금지)\n"
    "- 계단(stair): '1층', '입구 평지', '엘리베이터', '유모차 편함' → yes / "
    "'계단 많음', '2층 계단', '가파른' → no\n"
    "- 메뉴(menu): '아이 메뉴', '유아용', '안 매운', '순한 맛' → yes / "
    "'전부 매움', '아이 먹을 게 없음' → no\n"
    "\n"
    "[근거 리뷰 번호 규칙 — 매우 중요]\n"
    "- 각 리뷰에는 [1], [2] ... 번호가 붙어 있습니다.\n"
    "- 반드시 '[가게명]에 적힌 바로 그 가게'를 다룬 리뷰만 근거로 삼으세요.\n"
    "  (다른 가게 후기나 '지역 맛집 리스트' 같은 일반 글은 근거로 쓰지 마세요.)\n"
    "- 판정의 '결정적 근거'가 된 리뷰 하나의 번호를 stair_source_index / menu_source_index 에 적으세요.\n"
    "- 근거가 없어 unknown 으로 판정했거나, 그 가게를 다룬 리뷰가 하나도 없으면 index 를 0 으로 두세요."
)


def _decide_pass(place: Place, req: TripRequirements) -> bool:
    """요구조건에 비추어 이 후보가 '모든 조건 통과'인지 결정하는 순수 함수.

    핵심 정책(요구사항 반영):
        - 사용자가 요구한 조건은 반드시 'yes' 여야 통과.
        - 요구한 조건이 'no'(미달)이거나 'unknown'(불확실)이면 Drop.
        - 사용자가 요구하지 않은 조건은 판정과 무관하게 통과에 영향 없음.
    """
    if req.need_no_stairs and place.stair_status != "yes":
        return False
    if req.need_kid_friendly and place.menu_status != "yes":
        return False
    return True


def _format_reviews(results: list[dict]) -> str:
    """검색 결과 리스트를 LLM 입력용 '번호 매긴' 텍스트로 만든다.

    번호(1부터)는 results 의 인덱스+1 과 정확히 일치해야 하므로 순서를 그대로 유지합니다.
    → LLM 이 돌려준 *_source_index 를 _pick_source 에서 그대로 URL 로 되돌릴 수 있습니다.
    """
    if not results:
        return "(근거 없음)"
    return "\n".join(
        f"[{i}] {r.get('title', '')}: {r.get('body', '')}" for i, r in enumerate(results, 1)
    )


def _pick_source(results: list[dict], index: int, status: str) -> str:
    """LLM 이 고른 근거 번호(index)를 그 리뷰의 URL 로 되돌린다. (근거-링크 일치 보장)

    잘못된 링크를 다는 것보다 '링크를 안 다는 것'이 낫다는 원칙으로, 아래 경우엔 빈 문자열:
        - 판정이 unknown (결정적 근거가 없음)
        - index 가 0 이거나 유효 범위를 벗어남 (LLM 이 근거를 특정하지 못함)
    """
    if status == "unknown" or not results:
        return ""
    if not isinstance(index, int) or index < 1 or index > len(results):
        return ""
    return (results[index - 1].get("href") or "").strip()


def validate_place(place: Place, req: TripRequirements, label: str = "") -> bool:
    """후보 1곳의 계단/메뉴 조건을 타겟 검색 + LLM 판독으로 검증한다. (place 를 직접 수정)

    /search/step 이 후보를 '한 개씩' 검증할 때 호출하는 핵심 함수입니다.
        1) 요구한 조건만 타겟 검색 (예: "식당명 어린이 메뉴 site:blog.naver.com")
        2) 모은 리뷰 텍스트를 LLM 이 yes/no/unknown 으로 판정
        3) _decide_pass 정책으로 place.passed 결정

    Args:
        label: 진행 문구 앞에 붙일 접두사(예: "3번째 후보 · "). 비워도 됩니다.
    Returns:
        모든 조건을 통과했으면 True (place.passed 와 동일).
    """
    region = req.region or ""
    _emit(f"{label}'{place.name}' 검증을 시작합니다.")

    # --- 1) 조건별 타겟 검색 (요구한 조건만 검색해 비용 절감) ---
    #  개별 리뷰 결과(리스트)를 받아 두었다가, LLM 이 지목한 '근거 리뷰 번호'의 URL 만
    #  '자세히 보기' 링크로 씁니다. (근거-링크 불일치/엉뚱한 가게 링크 방지)
    stair_results: list[dict] = []
    menu_results: list[dict] = []
    if req.need_no_stairs:
        _emit(f"{label}'{place.name}'의 계단·접근성 정보를 조회 중...")
        stair_results = targeted_review_search(
            place.name, region, "계단 유아차 엘리베이터 입구 층"
        )
    if req.need_kid_friendly:
        _emit(f"{label}'{place.name}'의 안 매운·어린이 메뉴 정보를 조회 중...")
        menu_results = targeted_review_search(
            place.name, region, "어린이 메뉴 안매운 아이 유아"
        )

    # --- 2) LLM 판독 (근거가 없으면 unknown) ---
    _emit(f"{label}'{place.name}' 리뷰를 읽고 조건을 판정 중...")
    try:
        evidence_text = (
            f"[가게명] {place.name} ({region})\n"
            f"[계단 관련 리뷰]\n{_format_reviews(stair_results)}\n\n"
            f"[메뉴 관련 리뷰]\n{_format_reviews(menu_results)}"
        )
        verdict: ConditionCheck = condition_checker.invoke(
            [
                SystemMessage(content=VALIDATOR_SYSTEM),
                HumanMessage(content=evidence_text),
            ]
        )
        place.stair_status = verdict.stair_status
        place.stair_note = verdict.stair_note
        place.menu_status = verdict.menu_status
        place.menu_note = verdict.menu_note
        # ★ 근거 번호 → 그 리뷰의 URL 로 되돌려 출처를 연결 (판정에 실제 쓰인 글만)
        place.stair_source = _pick_source(
            stair_results, verdict.stair_source_index, verdict.stair_status
        )
        place.menu_source = _pick_source(
            menu_results, verdict.menu_source_index, verdict.menu_status
        )
    except Exception as e:
        # 판정 실패 시: 안전하게 unknown 유지 (Drop 대상)
        print(f"[validate_place] '{place.name}' 판정 실패:", e)
        place.stair_note = place.stair_note or "정보 확인이 어려웠습니다."
        place.menu_note = place.menu_note or "정보 확인이 어려웠습니다."

    # --- 3) 통과 판정 ---
    place.passed = _decide_pass(place, req)
    if place.passed:
        print(f"[validate_place] 통과 ✅ {place.name}")
        _emit(f"{label}'{place.name}' → 조건 충족, 목록에 추가합니다! ✅")
    else:
        print(f"[validate_place] 탈락 ❌ {place.name} "
              f"(계단={place.stair_status}, 메뉴={place.menu_status})")
        _emit(f"{label}'{place.name}' → 조건 미충족/불확실, 제외합니다. ❌")
    return place.passed


# ══════════════════════════════════════════════════════════════════════════
# 지도 생성 (Map Builder) + 안내 문구 — 그래프 노드가 아니라 /search/step 이 통과 후 호출
#  - 통과 후보의 좌표를 구해 Folium 지도로 만들고, 대화용 짧은 안내 문구를 만듭니다.
# ══════════════════════════════════════════════════════════════════════════
def _condition_label(status: str) -> str:
    """yes/no/unknown 상태를 사람이 읽는 한글 배지로 변환."""
    return {"yes": "충족 ✅", "no": "미충족 ❌", "unknown": "확인필요 ❓"}.get(status, "확인필요 ❓")


def _polish_first_line(base: str) -> str:
    """안내문의 '첫 문장만' writer_llm 으로 살짝 다듬는다. 실패하면 원문 그대로."""
    try:
        polished = writer_llm.invoke(
            [
                SystemMessage(content="아래 안내문의 '첫 문장만' 따뜻하고 자연스러운 한국어로 다듬어 한 줄로 답하세요. 목록은 그대로 둡니다."),
                HumanMessage(content=base),
            ]
        )
        first_line = (polished.content or "").strip().splitlines()[0]
        if first_line:
            rest = "\n".join(base.splitlines()[1:])
            return f"{first_line}\n{rest}"
    except Exception as e:
        print("[summary] 문구 다듬기 실패:", e)
    return base


def build_search_summary(
    req: TripRequirements | None,
    finalists: list[Place],
    *,
    done: bool,
    exhausted: bool,
    stopped: bool,
) -> str:
    """검색이 끝났을 때(목표 5곳 달성/중단/후보 소진) 대화창에 보여줄 '짧은' 안내 문구.

    ※ 상세 목록은 우측 결과 패널에서 보여주므로, 대화창에는 결과를 나열하지 않습니다.
    """
    region = req.region if req else ""
    n = len(finalists)

    if n == 0:
        return (
            f"'{region}'에서 조건을 '모두' 통과한 곳을 아직 찾지 못했어요. "
            "지역을 조금 넓히거나 조건을 완화해서 다시 알려주시겠어요?"
        )

    if stopped:
        base = f"검색을 멈췄어요. 지금까지 조건을 모두 통과한 {n}곳을 오른쪽 지도와 목록에서 확인해 보세요."
    elif done:
        base = f"조건을 모두 통과한 {n}곳을 모두 채웠어요! 🎉 오른쪽 지도와 목록에서 확인해 보세요."
    elif exhausted:
        base = f"더 이상 새로운 후보가 없어, 조건을 모두 통과한 {n}곳을 정리했어요. 오른쪽에서 확인해 보세요."
    else:
        base = f"조건을 모두 통과한 {n}곳이에요. 오른쪽 지도와 목록에서 확인해 보세요."
    return _polish_first_line(base)


# 지역 중심 좌표 캐시 (같은 지역을 여러 번 지오코딩하지 않도록 — 재호출 속도↑)
_center_cache: dict[str, tuple[float, float]] = {}


def _region_center(region: str) -> tuple[float, float]:
    """지역명을 지오코딩해 지도 '검색 중심' 좌표를 돌려준다(캐시 사용, 실패 시 서울시청)."""
    key = region or "서울"
    if key in _center_cache:
        return _center_cache[key]
    center = geocode_place(f"{key}, 대한민국") or (37.5665, 126.9780)
    _center_cache[key] = center
    return center


def build_map_html(
    req: TripRequirements | None,
    finalists: list[Place],
    radius_km: float = SEARCH_RADIUS_KM,
) -> str:
    """검색 영역(원)과 '위치가 확실한 후보'의 핀만 그린 Folium 지도 HTML 을 만든다.

    핀 정확도 정책 (요청 사항 반영)
        - 지도에는 먼저 '검색 영역'을 원(Circle)으로 그립니다. (지역 중심 반경 radius_km)
        - 각 후보는 locate_place 로 '지역 반경 안에서 확실히' 위치를 찾은 경우에만 핀을 찍습니다.
          (정확한 위치를 모르면 핀을 생략 → 엉뚱한 자리에 잘못 찍지 않음)
        - locate_place 는 KAKAO_REST_API_KEY 가 있으면 카카오 로컬 검색으로 정확도가 크게 오르고,
          없으면 Nominatim 결과가 지역 반경 안일 때만 채택합니다.
        - 지오코딩은 후보당 한 번만 시도(geo_checked)하고 결과(located, lat/lng)를 캐시합니다.

    Args:
        radius_km: 검색 영역 반경(km). 원 크기와 핀 판정 반경에 '동일하게' 적용됩니다.
            프런트 슬라이더로 조절하며, 카카오 검색 상한(20km)에 맞춰 1~20 로 clamp 합니다.
    실패해도 빈 문자열을 돌려주어 앱이 멈추지 않습니다.
    """
    region = req.region if req else "서울"
    center = _region_center(region)

    # 원(그림)과 locate_place(판정)에 같은 값을 쓰도록 여기서 한 번 더 방어적으로 clamp.
    #  (카카오 로컬 검색 반경 상한이 20km 이므로 그보다 크게 그리면 원-검색이 어긋남)
    radius_km = max(1.0, min(float(radius_km), 20.0))

    try:
        fmap = folium.Map(location=list(center), zoom_start=14, tiles="OpenStreetMap")

        # (1) 검색 영역을 원으로 표시 — "이 근방을 찾고 있어요" 를 한눈에
        folium.Circle(
            location=list(center),
            radius=radius_km * 1000,  # m 단위
            color="#3186cc",
            weight=2,
            fill=True,
            fill_opacity=0.08,
            popup=folium.Popup(f"검색 영역: '{region}' 중심 반경 약 {radius_km:.0f}km", max_width=220),
        ).add_to(fmap)
        # 지역 중심 표식(작게)
        folium.Marker(
            location=list(center),
            tooltip=f"{region} (검색 중심)",
            icon=folium.Icon(color="blue", icon="search", prefix="fa"),
        ).add_to(fmap)

        used_coords: set[tuple[float, float]] = set()
        for p in finalists:
            # 후보당 한 번만 위치 확인 (재호출 시에는 캐시된 결과 사용)
            if not p.geo_checked:
                p.geo_checked = True
                coords = locate_place(p.name, p.address, region, center, radius_km)
                if coords is not None:
                    p.lat, p.lng = coords
                    p.located = True
                else:
                    p.located = False  # 정확한 위치 미확인 → 핀 생략

            # 위치가 확실하지 않으면 핀을 찍지 않고 넘어감
            if not p.located or p.lat is None or p.lng is None:
                continue

            lat, lng = p.lat, p.lng
            # 드물게 두 곳이 같은 좌표면 아주 살짝만 흔들어 겹침 방지 (약 40m)
            if (round(lat, 5), round(lng, 5)) in used_coords:
                lat += 0.0004
                lng += 0.0004
            used_coords.add((round(lat, 5), round(lng, 5)))

            # 팝업 HTML: 이름 + '요구한 조건만' 배지·근거·자세히 보기 링크
            #  (요구하지 않은 조건은 표시하지 않습니다 — 결과를 요청 조건에 집중)
            popup_rows = [f"<b>{p.name}</b>", f"<span style='color:gray'>{p.category or ''}</span>"]
            if req and req.need_no_stairs:
                popup_rows.append(f"계단: {_condition_label(p.stair_status)}")
                if p.stair_note:
                    popup_rows.append(f"<small>· {p.stair_note}</small>")
                if p.stair_source:
                    popup_rows.append(
                        f"<small><a href='{p.stair_source}' target='_blank'>자세히 보기</a></small>"
                    )
            if req and req.need_kid_friendly:
                popup_rows.append(f"메뉴: {_condition_label(p.menu_status)}")
                if p.menu_note:
                    popup_rows.append(f"<small>· {p.menu_note}</small>")
                if p.menu_source:
                    popup_rows.append(
                        f"<small><a href='{p.menu_source}' target='_blank'>자세히 보기</a></small>"
                    )
            popup_html = "<br>".join(popup_rows)

            # 조건을 모두 통과한 확정 후보이므로 초록 핀으로 표시
            folium.Marker(
                location=[lat, lng],
                popup=folium.Popup(popup_html, max_width=260),
                tooltip=p.name,
                icon=folium.Icon(color="green", icon="cutlery", prefix="fa"),
            ).add_to(fmap)

        return fmap.get_root().render()
    except Exception as e:
        print("[build_map_html] 지도 생성 실패:", e)
        return ""
