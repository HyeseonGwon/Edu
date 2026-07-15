"""
config.py
=========
프로젝트 전역에서 공통으로 쓰는 "환경 설정"과 "LLM 팩토리"를 모아 둔 모듈입니다.

발표(PT) 포인트
---------------
- LLM 모델명을 이 파일 한 곳에서만 관리하므로, 데모 후 gpt-4o-mini ↔ gpt-4o
  교체가 한 줄이면 끝납니다. (노드 코드는 전혀 손댈 필요 없음)
- .env 로드 경로를 여러 곳 시도해, "어느 폴더에서 실행하든" API 키를 찾도록 했습니다.
  (Slack 실습 slack_app.py 에서 쓰던 load_dotenv 다중 로드 패턴과 동일한 철학)
"""

import os
from pathlib import Path

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

# ──────────────────────────────────────────────────────────────────────────
# 1) .env 로드
#    - 이 파일 기준 상위 폴더(과제/), 그 상위(실습/), 현재 작업 폴더 순으로 .env 를 찾습니다.
#    - 실습 워크스페이스의 .env 는 실습/ 루트에 있으므로 parent.parent.parent 가 핵심입니다.
# ──────────────────────────────────────────────────────────────────────────
_HERE = Path(__file__).resolve()
_ENV_CANDIDATES = [
    _HERE.parent.parent / ".env",          # 과제/.env (있다면)
    _HERE.parent.parent.parent / ".env",   # 실습/.env  (실제 위치)
    Path.cwd() / ".env",                    # 실행 위치의 .env
]
for _candidate in _ENV_CANDIDATES:
    if _candidate.exists():
        load_dotenv(_candidate)
load_dotenv()  # 표준 위치(현재 경로 상위 탐색)도 한 번 더 시도

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# (선택) 카카오 로컬 API 키 — 지도 핀 정확도를 크게 높입니다.
#  - .env 에 KAKAO_REST_API_KEY=... 를 넣으면 agent/tools.py 의 locate_place 가
#    카카오 로컬 검색으로 '지역 반경 안의 실제 좌표'를 찾습니다.
#  - 없어도 동작합니다(무료 Nominatim 으로 폴백하며, 확실할 때만 핀을 찍음).
KAKAO_REST_API_KEY = os.getenv("KAKAO_REST_API_KEY")

# ──────────────────────────────────────────────────────────────────────────
# 2) 모델 상수
#    - REASONING_MODEL : 후보 생성/심층 검증/요약 등 "판단"이 필요한 곳에 쓰는 모델.
#      데모에서는 속도·비용을 위해 gpt-4o-mini 를 기본값으로 둡니다.
#      발표/실사용 시 'gpt-4o' 로 바꾸면 판단 품질이 올라갑니다.
# ──────────────────────────────────────────────────────────────────────────
REASONING_MODEL = "gpt-4o-mini"


def make_llm(temperature: float = 0.0, model: str = REASONING_MODEL) -> ChatOpenAI:
    """공통 규격의 ChatOpenAI 인스턴스를 만들어 주는 팩토리 함수.

    Args:
        temperature: 창의성(무작위성). 정보 추출·검증은 0에 가깝게, 문장 다듬기는 살짝 높게.
        model: 사용할 OpenAI 모델명. 기본은 REASONING_MODEL.

    Returns:
        ChatOpenAI: 노트북 실습(15.3/15.4)에서 쓰던 것과 동일한 LangChain LLM 객체.
    """
    return ChatOpenAI(model=model, temperature=temperature, api_key=OPENAI_API_KEY)
