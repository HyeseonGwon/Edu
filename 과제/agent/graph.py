"""
graph.py
========
LangGraph 워크플로우를 '조립'하는 파일입니다.
(15.1~15.4 실습에서 배운 StateGraph / add_node / add_edge 패턴)

그래프 구조
-----------
    START
      │
      ▼
  state_manager ──► END

이 프로젝트에서 그래프의 역할은 '대화형 상태 관리(요구조건 수집)' 입니다.
    - 정보가 부족하면 사용자에게 되묻고 이번 턴을 종료(stage='collecting').
    - 3가지(누구와/무엇을/어디로)가 모두 모이면 검색 준비 완료(stage='ready').

실제 후보 검색·검증은 그래프가 아니라 FastAPI 의 /search/step 엔드포인트가
'후보를 한 개씩' 반복 처리합니다. (조건을 모두 통과하면 즉시 목록에 추가)
이렇게 나눈 이유: 사용자가 결과를 하나씩 실시간으로 받아보고, 언제든
'그만 찾기'로 멈출 수 있어야 하기 때문입니다. (그래프 invoke 는 원자적 실행이라
중간에 부분 결과를 흘려보내며 멈추기가 어렵습니다.)
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from .nodes import state_manager_node
from .state import FamilyTripState


def build_graph():
    """가족 여행 코디네이터 그래프를 생성하고 compile 하여 반환한다.

    Returns:
        compile 된 LangGraph 앱. app.invoke(FamilyTripState(...)) 로 실행합니다.
    """
    # StateGraph 에 우리가 정의한 State 스키마를 전달 (모든 노드가 이 State 를 공유)
    workflow = StateGraph(FamilyTripState)

    # 노드 등록 — 함수 자체를 넘깁니다(괄호 없이). 15.1에서 강조된 부분.
    workflow.add_node("state_manager", state_manager_node)

    # 엣지 연결: START → state_manager → END
    #   (state_manager 가 되묻기/검색준비 여부를 stage 로 알려주므로 분기 불필요)
    workflow.add_edge(START, "state_manager")
    workflow.add_edge("state_manager", END)

    # compile: 실행 가능한 그래프로 확정
    return workflow.compile()


# 모듈 로드 시 한 번만 그래프를 만들어 재사용 (FastAPI 매 요청마다 다시 만들지 않도록)
trip_graph = build_graph()
