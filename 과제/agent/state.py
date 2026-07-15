"""
state.py
========
LangGraph 가 그래프 전체에서 공유하는 "메모리" 인 State 와,
그 State 안에 담기는 구조화된 데이터 모델(Pydantic BaseModel)을 정의합니다.

발표 포인트
-----------
- 15.1 실습에서 배운 대로, State 는 "모든 노드가 읽고/쓰는 공용 저장소" 입니다.
- 대화 메시지는 15.2/15.3 에서 배운 add_messages 리듀서를 붙여,
  각 노드가 '새로 추가할 메시지'만 반환해도 자동으로 누적되게 했습니다.
- 검증 결과처럼 구조가 중요한 값은 TypedDict 가 아니라 pydantic BaseModel 로 강제해
  (15.1의 교훈) 잘못된 형태가 흘러들어오는 것을 막습니다.
"""

from __future__ import annotations

from typing import Annotated, Literal, Optional

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field


# ──────────────────────────────────────────────────────────────────────────
# 1) 사용자 요구조건 (State Manager 노드가 대화에서 뽑아내는 구조화 결과)
# ──────────────────────────────────────────────────────────────────────────
class TripRequirements(BaseModel):
    """대화 3단계(누구와 / 무엇을 / 어디로)에서 추출한 '구조화된 조건'.

    이 객체가 곧 후보 탐색·심층 검증의 '검색 사양서' 역할을 합니다.
    """

    companions: str = Field(default="", description="누구와 함께 가는지 (예: 3세대 가족, 아기·노부모 동반)")
    place_type: str = Field(default="", description="무엇을 찾는지 (예: 식당, 숙소, 식당+숙소)")
    region: str = Field(default="", description="어디로 가는지 = 검색 대상 지역 (예: 서울 송파구, 강남 일대)")
    menu: str = Field(
        default="",
        description="원하는 음식/메뉴 (식당일 때만 의미 있는 '선택' 조건, 예: 삼겹살·파스타·국밥). 없으면 빈 문자열",
    )

    # ↓ 심층 검증에서 실제로 확인할 '2가지 핵심 가변 메타데이터' 스위치
    need_no_stairs: bool = Field(
        default=False,
        description="계단이 적어야 하는지 여부 (유모차·휠체어·노부모 동반 시 True)",
    )
    need_kid_friendly: bool = Field(
        default=False,
        description="어린이 메뉴 또는 안 매운 메뉴가 필요한지 여부 (아이 동반 시 True)",
    )
    extra_notes: str = Field(default="", description="기타 요청사항 (주차, 예산, 룸 유무 등)")


# ──────────────────────────────────────────────────────────────────────────
# 2) 장소(식당/숙소) 후보 1건을 표현하는 모델
#    - 후보 생성 → 심층 검증 → 지도 생성까지 이 객체 하나가 계속 채워집니다.
# ──────────────────────────────────────────────────────────────────────────
class Place(BaseModel):
    """식당/숙소 후보 1곳. 파이프라인을 거치며 검증 결과와 좌표가 채워집니다."""

    # --- 후보 생성 단계에서 채워지는 기본 정보 ---
    name: str = Field(description="장소 이름")
    category: str = Field(default="", description="분류 (식당/숙소/카페 등)")
    address: str = Field(default="", description="주소 또는 위치 설명")
    reason: str = Field(default="", description="이 장소를 후보로 뽑은 간단한 이유")
    # 발굴 단계에서 채워지는 '선택' 신호: 사용자가 원한 메뉴(예: 삼겹살)를 파는 근거가
    #  검색 스니펫에 있으면 True. (계단/어린이메뉴 검증과 무관한, 단순 '메뉴 일치' 표시용)
    menu_match: bool = Field(
        default=False,
        description="사용자가 원한 '메뉴'(예: 삼겹살)를 판다는 근거가 스니펫에 있으면 True. 메뉴 조건이 없거나 식당이 아니면 False",
    )

    # --- 심층 검증 단계에서 채워지는 결과 ---
    # 'yes'(조건 충족) / 'no'(미충족) / 'unknown'(근거 부족) 3단계로 판정합니다.
    stair_status: Literal["yes", "no", "unknown"] = Field(
        default="unknown", description="계단 조건 판정 (yes=계단 적음/접근성 양호)"
    )
    stair_note: str = Field(default="", description="계단 판정 근거 요약")
    stair_source: str = Field(default="", description="계단 정보를 확인한 대표 출처 URL('자세히 보기')")
    menu_status: Literal["yes", "no", "unknown"] = Field(
        default="unknown", description="어린이/안매운 메뉴 판정 (yes=있음)"
    )
    menu_note: str = Field(default="", description="메뉴 판정 근거 요약")
    menu_source: str = Field(default="", description="메뉴 정보를 확인한 대표 출처 URL('자세히 보기')")
    passed: bool = Field(default=False, description="최종 통과 여부")

    # --- 지도 생성 단계에서 채워지는 좌표/표시 여부 ---
    lat: Optional[float] = Field(default=None, description="위도")
    lng: Optional[float] = Field(default=None, description="경도")
    geo_checked: bool = Field(default=False, description="지오코딩(위치 확인)을 이미 시도했는지")
    located: bool = Field(
        default=False,
        description="지역 반경 안에서 '확실히' 위치를 찾았는지 (True 일 때만 지도에 핀 표시)",
    )


# ──────────────────────────────────────────────────────────────────────────
# 3) 그래프 전체 State
# ──────────────────────────────────────────────────────────────────────────
class FamilyTripState(BaseModel):
    """가족 여행 코디네이터 그래프의 공용 State.

    한 번의 invoke() 동안 노드들이 이 State 를 주고받으며 값을 채웁니다.
    FastAPI 세션(main.py)에서 이 State 의 일부(대화·요구조건·결과)를 저장했다가
    다음 턴에 이어서 넣어 주므로, 멀티턴 대화가 가능합니다.
    """

    # ── 대화 관련 ──
    # add_messages 리듀서: 노드가 [새 메시지]만 반환해도 기존 history 뒤에 이어 붙습니다.
    chat_history: Annotated[list[BaseMessage], add_messages] = Field(default_factory=list)
    user_input: str = Field(default="", description="이번 턴 사용자가 입력한 원문")

    # ── State Manager 산출물 ──
    requirements: Optional[TripRequirements] = Field(default=None, description="추출된 구조화 조건")
    info_complete: bool = Field(default=False, description="3단계 정보가 모두 모였는지")
    follow_up_question: str = Field(default="", description="정보 부족 시 사용자에게 되물을 질문")

    # ── 검색 산출물 ──
    #  실제 후보 검증은 /search/step 이 하지만, 결과 저장 위치로 함께 둡니다.
    finalists: list[Place] = Field(default_factory=list, description="조건을 모두 통과한 최종 후보(최대 5곳)")
    map_html: str = Field(default="", description="Folium 지도 HTML (우측 패널 렌더링용)")

    # ── UI/제어용 ──
    # stage: 프런트엔드가 현재 어느 단계인지 표시하는 데 사용
    #  - collecting: 정보 수집 중(되물음)
    #  - ready: 정보가 모두 모여 검색을 시작할 준비 완료(프런트가 /search/step 반복 호출)
    #  - searching/done/error: 진행/완료/오류 (호환용)
    stage: Literal["collecting", "ready", "searching", "done", "error"] = Field(default="collecting")
    assistant_message: str = Field(default="", description="이번 턴에 사용자에게 보여줄 최종 답변 텍스트")
