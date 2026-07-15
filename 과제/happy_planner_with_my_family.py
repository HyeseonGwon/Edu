"""
happy_planner_with_my_family.py  (CLI 백업 데모)
==============================================
UI(Streamlit) 없이 '터미널에서 바로' 에이전트를 시연/검증할 수 있는 스크립트입니다.
발표 중 UI 가 안 뜨는 상황을 대비한 백업이자, 파이프라인 로직만 빠르게 확인하는 용도입니다.

- 대화(요구조건 수집)는 agent/graph.py 의 trip_graph 로 처리합니다. (main.py 와 동일한 두뇌)
- 정보가 모두 모이면(stage='ready') 후보를 '한 개씩' 검색·검증하며
  조건을 모두 통과한 곳을 최종 5곳까지 채웁니다. (main.py 의 /search/step 과 동일한 로직)
- 15.3 실습의 input() 기반 멀티턴 대화 루프 패턴을 따랐습니다.

실행:
    conda activate day4
    cd 과제
    python happy_planner_with_my_family.py
"""

from __future__ import annotations

from agent.graph import trip_graph
from agent.nodes import (
    MAX_CANDIDATES_TOTAL,
    REFILL_SIZE,
    TARGET_FINALISTS,
    build_search_summary,
    search_candidates,
    validate_place,
)
from agent.state import FamilyTripState, Place, TripRequirements


def run_search(req: TripRequirements) -> list[Place]:
    """후보를 '한 개씩' 검색·검증해 조건을 모두 통과한 곳을 최대 5곳 모아 돌려준다.

    main.py 의 /search/step 이 세션 상태로 반복 수행하는 것을,
    여기서는 하나의 while 루프로 압축해 동일한 순서로 실행합니다.
    """
    seen: set[str] = set()        # 이미 검색으로 뽑은 이름(중복 방지)
    queue: list[Place] = []       # 검색으로 뽑았지만 아직 검증 안 한 후보
    finalists: list[Place] = []   # 조건 모두 통과(최대 5곳)
    tried = 0                     # 검증한 후보 수(안전 상한 판정)
    refill = 0                    # 후보 큐 리필 횟수(검색어 변화용)

    while len(finalists) < TARGET_FINALISTS and tried < MAX_CANDIDATES_TOTAL:
        # 큐가 비면 새로 검색해 리필
        if not queue:
            new = search_candidates(
                req, target_count=REFILL_SIZE, exclude_names=seen, refill_index=refill
            )
            refill += 1
            for c in new:
                seen.add(c.name)
            queue.extend(new)
            if not queue:  # 더 이상 새 후보가 없음 → 소진
                print("  (더 이상 새로운 후보를 찾지 못했어요.)")
                break

        # 후보 한 개를 꺼내 검증 → 조건 모두 통과하면 바로 목록에 추가
        place = queue.pop(0)
        tried += 1
        passed = validate_place(
            place, req, label=f"(확정 {len(finalists)}/{TARGET_FINALISTS}) "
        )
        if passed and place.name not in {p.name for p in finalists}:
            finalists.append(place)
            print(f"  ✅ 목록 추가: {place.name}  (지금까지 {len(finalists)}곳)")

    return finalists


def main():
    print("=" * 60)
    print("👵👦 3세대 가족 여행 코디네이터 (CLI 데모)")
    print("   누구와 / 무엇을(식당) / 어디로 를 알려주세요.")
    print("   종료하려면 q 또는 quit 입력")
    print("=" * 60)

    # 세션 상태: 대화·요구조건을 턴 사이에 유지 (main.py 의 세션 저장소와 같은 역할)
    chat_history: list = []
    requirements = None

    while True:
        user_input = input("\n[나] ").strip()
        if user_input.lower() in ("q", "quit", "exit", "종료"):
            print("이용해 주셔서 감사합니다. 즐거운 여행 되세요!")
            break
        if not user_input:
            continue

        # 이전 상태를 복원해 그래프 실행 (FastAPI /chat 과 동일한 흐름)
        init_state = FamilyTripState(
            chat_history=chat_history,
            user_input=user_input,
            requirements=requirements,
        )
        result = trip_graph.invoke(init_state)

        # 상태 갱신
        chat_history = result.get("chat_history", chat_history)
        requirements = result.get("requirements", requirements)

        # 에이전트 응답 출력
        print(f"\n[코디네이터] {result.get('assistant_message', '')}")

        # 정보가 모두 모이면(ready) 후보 검색 루프를 실행하고 결과를 출력
        if result.get("stage") == "ready" and requirements is not None:
            print("\n  --- 후보를 하나씩 확인합니다 ---")
            finalists = run_search(requirements)

            summary = build_search_summary(
                requirements,
                finalists,
                done=len(finalists) >= TARGET_FINALISTS,
                exhausted=False,
                stopped=False,
            )
            print(f"\n[코디네이터] {summary}")

            if finalists:
                print("\n  --- 최종 추천 ---")
                for i, p in enumerate(finalists, 1):
                    print(f"  {i}. {p.name} "
                          f"(계단={p.stair_status}, 메뉴={p.menu_status})")


if __name__ == "__main__":
    main()
