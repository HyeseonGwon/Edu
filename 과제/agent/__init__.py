"""
agent 패키지
============
3세대 맞춤형 가족 여행 코디네이터의 LangGraph 에이전트 구성요소를 담은 패키지입니다.

- state.py  : State 및 데이터 모델(TripRequirements, Place)
- tools.py  : 웹 검색(DuckDuckGo) / 타겟 리뷰 검색 / 지오코딩 도구
- nodes.py  : State Manager 노드 + 후보 검색/검증/지도 생성 헬퍼
- graph.py  : 노드를 연결한 LangGraph 워크플로우(대화형 상태 관리)
- config.py : 공용 설정 및 LLM 팩토리
"""

from .graph import build_graph, trip_graph
from .nodes import (
    MAX_CANDIDATES_TOTAL,
    REFILL_SIZE,
    TARGET_FINALISTS,
    build_map_html,
    build_search_summary,
    check_open_today,
    reset_progress_sink,
    search_candidates,
    set_progress_sink,
    sort_finalists,
    validate_place,
)
from .state import FamilyTripState, Place, TripRequirements

__all__ = [
    "build_graph",
    "trip_graph",
    "FamilyTripState",
    "Place",
    "TripRequirements",
    # 후보를 '한 개씩' 검증하는 검색 루프용 유틸 (main.py 의 /search/step 에서 사용)
    "search_candidates",
    "validate_place",
    "check_open_today",
    "build_map_html",
    "build_search_summary",
    "set_progress_sink",
    "reset_progress_sink",
    "sort_finalists",
    "TARGET_FINALISTS",
    "REFILL_SIZE",
    "MAX_CANDIDATES_TOTAL",
]
