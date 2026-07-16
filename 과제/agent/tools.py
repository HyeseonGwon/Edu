"""
tools.py
========
에이전트가 "바깥 세상"의 정보를 가져올 때 쓰는 도구(Tool) 모음입니다.

여기 있는 도구는 아래와 같습니다.
1) web_search_raw     : DuckDuckGo 로 일반 웹 검색 → 파이썬 리스트[dict] 반환 (후보군 탐색용)
2) targeted_review_search : "식당명 + 조건 + site:blog.naver.com" 형태의 타겟 검색 → 개별 리뷰 결과 리스트[{title,body,href}] 반환 (심층 검증용)
3) geocode_place      : 장소명/주소 → 위경도 (실패 시 지역 중심으로 '폴백') — 지도 중심 잡기용
4) locate_place(_info): 장소를 '지역 반경 안에서 확실히' 찾을 때만 좌표/상세 반환(아니면 None) — 정확한 핀용
                        (KAKAO_REST_API_KEY 가 있으면 카카오 로컬 검색으로 정확도↑ + place_url 확보, 없으면 Nominatim)
5) fetch_place_hours_text : 카카오 장소 상세(place_url/id)에서 영업시간·휴무 텍스트를 긁어옴 — '오늘 영업 여부' 판정용

지도 핀 정확도 (개선 포인트)
--------------------------
- 무료 지오코딩(Nominatim)은 국내 상호명을 잘 못 찾습니다. 그래서 '대충 찍기'보다
  locate_place 로 '지역 반경 안에서 확실히 찾은 곳만' 핀을 찍고, 나머지는 핀을 생략합니다.
- 선택적으로 .env 에 KAKAO_REST_API_KEY 를 넣으면 카카오 로컬 검색으로 훨씬 정확해집니다.

설계 의도 (발표 포인트)
----------------------
- 15.4 Tool 실습에서는 @tool + LLM 바인딩(ReAct) 방식을 썼지만,
  이 파이프라인에서는 "언제 어떤 검색을 할지"를 우리가 결정적으로 제어하고 싶기 때문에
  도구를 '일반 파이썬 함수'로 직접 호출합니다. (더 예측 가능하고 디버깅이 쉬움)
- 그래도 학습한 @tool 패턴을 보여주기 위해, web_search 를 @tool 로도 감싸 두었습니다.
- 실시간 시연 안정성을 위해 "모든 도구는 절대 예외를 밖으로 던지지 않습니다."
  실패하면 빈 결과/None 을 돌려주고, 판단은 상위 노드가 하도록 합니다. (Fallback 우선)
"""

from __future__ import annotations

import json
import math
import os
import re
import time

import requests
from langchain_core.tools import tool

# ddgs(신) / duckduckgo_search(구) 어느 쪽이 깔려 있어도 동작하도록 방어적으로 import
try:  # 신규 패키지명
    from ddgs import DDGS
    from ddgs.exceptions import DDGSException
except Exception:  # pragma: no cover - 구버전 호환
    try:
        from duckduckgo_search import DDGS  # type: ignore
        DDGSException = Exception  # 구버전은 별도 예외 클래스가 없을 수 있음
    except Exception:
        DDGS = None  # 검색 자체가 불가능한 환경 (그래도 앱은 죽지 않음)
        DDGSException = Exception


# ──────────────────────────────────────────────────────────────────────────
# 1) 일반 웹 검색 (후보군 탐색용)
# ──────────────────────────────────────────────────────────────────────────
def web_search_raw(query: str, max_results: int = 8, region: str = "kr-kr") -> list[dict]:
    """DuckDuckGo 텍스트 검색을 실행하고 결과를 리스트[dict]로 돌려줍니다.

    각 dict 는 {'title', 'href', 'body'} 키를 가집니다.
    (body = 검색 스니펫. LLM이 후보 이름/성격을 파악하는 근거가 됩니다.)

    Args:
        query: 검색어.
        max_results: 최대 결과 수.
        region: DuckDuckGo 지역 코드. 한국 결과를 우선하려고 'kr-kr' 사용.

    Returns:
        검색 결과 리스트. 실패하거나 결과가 없으면 빈 리스트 [].
        (★ 예외를 던지지 않는 것이 핵심 — 시연 중 앱이 멈추지 않도록)
    """
    if DDGS is None:
        # 검색 패키지가 아예 없는 경우: 조용히 빈 결과 반환
        return []

    try:
        with DDGS() as ddgs:
            # ddgs.text 는 제너레이터이므로 list() 로 소진합니다.
            results = list(ddgs.text(query, region=region, max_results=max_results))
    except TypeError:
        # 일부 버전은 region 인자를 받지 않음 → region 없이 재시도
        try:
            with DDGS() as ddgs:
                results = list(ddgs.text(query, max_results=max_results))
        except Exception:
            return []
    except DDGSException:
        # ddgs 는 일시 차단·결과 0건일 때 예외를 던짐 → 빈 결과로 처리
        return []
    except Exception:
        # 네트워크 등 기타 모든 예외도 흡수
        return []

    # 결과 dict 의 키 이름이 버전마다 조금 다를 수 있어 표준화(normalize)
    normalized: list[dict] = []
    for r in results:
        normalized.append(
            {
                "title": r.get("title") or r.get("heading") or "",
                "href": r.get("href") or r.get("link") or r.get("url") or "",
                "body": r.get("body") or r.get("snippet") or r.get("description") or "",
            }
        )
    return normalized


@tool
def web_search(query: str) -> str:
    """최신 웹 정보를 검색합니다. 질문이나 키워드를 넣으세요. (LangChain @tool 버전)

    15.4 실습의 web_search 와 동일한 형태(문자열 JSON 반환)로,
    향후 ReAct 방식(LLM이 스스로 검색을 결정)으로 확장할 때 그대로 재사용할 수 있습니다.
    """
    results = web_search_raw(query, max_results=3)
    if not results:
        return f"검색 결과 없음 (query: {query})"
    return json.dumps(results, ensure_ascii=False)


# ──────────────────────────────────────────────────────────────────────────
# 2) 타겟 리뷰 검색 (심층 검증용) — 가장 중요한 도구
# ──────────────────────────────────────────────────────────────────────────
def targeted_review_search(
    place_name: str, region: str, keyword: str, max_results: int = 4
) -> list[dict]:
    """특정 장소에 대해 "조건 키워드"를 겨냥한 검색을 수행해 '개별 리뷰 결과'를 돌려줍니다.

    예) place_name='OO식당', keyword='어린이 메뉴'
        → 검색어: "OO식당" 어린이 메뉴 site:blog.naver.com
        → 네이버 블로그 후기 결과들을 [{title, body, href}, ...] 리스트로 반환

    상위(validate_place)는 이 리스트를 '번호 매긴' 근거로 LLM에 넘기고, LLM이 '판정에
    실제로 사용한 리뷰의 번호'를 돌려주면 그 번호의 href 만 출처 링크로 씁니다.
    → 근거(스니펫)와 링크(URL)가 항상 같은 글을 가리키도록 보장합니다.

    Args:
        place_name: 식당/숙소 이름.
        region: 지역(동명이인 장소 구분을 돕기 위해 검색어에 함께 넣음).
        keyword: 확인하려는 조건 키워드 (예: '계단 유아차', '어린이 메뉴 안매운').
        max_results: 검색 결과 수.

    Returns:
        [{"title", "body", "href"}, ...] 형태의 결과 리스트(중복 href 제거).
        근거를 하나도 못 찾으면 빈 리스트 [].
    """
    # 1차 시도: 네이버 블로그를 겨냥한 검색 연산자(site:) 사용
    #  - 검색 연산자를 쓰면 후기가 풍부한 블로그 텍스트를 우선적으로 모을 수 있습니다.
    primary_query = f'"{place_name}" {keyword} site:blog.naver.com'
    results = web_search_raw(primary_query, max_results=max_results)

    # 2차 시도: site: 로 결과가 없으면 지역명을 붙여 일반 검색으로 폴백
    if not results:
        fallback_query = f"{place_name} {region} {keyword} 후기"
        results = web_search_raw(fallback_query, max_results=max_results)

    if not results:
        return []  # 근거 없음 → 상위 노드가 '불확실'로 처리

    # 결과를 표준화(중복 href 제거, 빈 항목 제외)해 '번호 매길 수 있는' 리스트로 정리.
    #  - 순서를 그대로 유지해야 LLM 이 돌려준 번호를 그대로 URL 로 되돌릴 수 있습니다.
    cleaned: list[dict] = []
    seen_href: set[str] = set()
    for r in results:
        title = (r.get("title") or "").strip()
        body = (r.get("body") or "").strip()
        href = (r.get("href") or "").strip()
        if not (title or body):
            continue
        dedup_key = href or title
        if dedup_key in seen_href:
            continue
        seen_href.add(dedup_key)
        # LLM 입력 토큰 낭비 방지를 위해 스니펫이 과도하게 길면 잘라냄
        cleaned.append({"title": title[:200], "body": body[:500], "href": href})
    return cleaned


# ──────────────────────────────────────────────────────────────────────────
# 3) 지오코딩 (지도 생성용): 장소명/주소 → 위경도
# ──────────────────────────────────────────────────────────────────────────

# 무료 Nominatim(OpenStreetMap) 지오코딩 엔드포인트.
# - API 키가 필요 없어 데모에 적합하지만, 국내 상호명은 못 찾는 경우가 많습니다.
# - 그래서 아래 SEOUL_AREA_COORDS 폴백 좌표를 함께 둡니다.
_NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

# 서울/수도권 주요 지역의 대략적인 중심 좌표 (지오코딩 실패 시 폴백용).
# - 상호를 못 찾아도 "지역 중심 근처"에는 핀을 찍어 지도를 비우지 않기 위함입니다.
SEOUL_AREA_COORDS: dict[str, tuple[float, float]] = {
    "강남": (37.4979, 127.0276),
    "역삼": (37.5006, 127.0364),
    "선릉": (37.5045, 127.0491),
    "삼성": (37.5090, 127.0631),
    "서초": (37.4837, 127.0324),
    "송파": (37.5145, 127.1060),
    "잠실": (37.5133, 127.1000),
    "강동": (37.5301, 127.1238),
    "성수": (37.5446, 127.0559),
    "성동": (37.5634, 127.0369),
    "마포": (37.5663, 126.9018),
    "홍대": (37.5563, 126.9236),
    "여의도": (37.5216, 126.9241),
    "용산": (37.5326, 126.9905),
    "이태원": (37.5347, 126.9947),
    "종로": (37.5729, 126.9794),
    "중구": (37.5636, 126.9976),
    "명동": (37.5636, 126.9850),
    "강북": (37.6396, 127.0257),
    "노원": (37.6542, 127.0568),
    "영등포": (37.5264, 126.8963),
    "구로": (37.4954, 126.8874),
    "관악": (37.4784, 126.9516),
    "동작": (37.5124, 126.9393),
    "광진": (37.5385, 127.0823),
    "서울": (37.5665, 126.9780),  # 최종 폴백: 서울 시청
}


def _fallback_coords(query: str) -> tuple[float, float] | None:
    """지오코딩이 실패했을 때, 지역명 키워드 매칭으로 대략 좌표를 추정합니다."""
    for area, coords in SEOUL_AREA_COORDS.items():
        if area in query:
            return coords
    return None


def geocode_place(query: str, session_delay: float = 1.0) -> tuple[float, float] | None:
    """장소명/주소 문자열을 위경도(lat, lng) 튜플로 변환합니다.

    처리 순서(모두 실패해도 예외 없이 None 또는 폴백 좌표 반환):
        1) Nominatim(OpenStreetMap) 에 검색 → 첫 결과의 좌표 사용
        2) 실패 시, SEOUL_AREA_COORDS 에서 지역 키워드로 폴백 좌표 추정
        3) 그래도 없으면 None

    Args:
        query: 예) "OO식당, 서울 강남구, 대한민국"
        session_delay: Nominatim 은 초당 1회 호출 정책이 있어 살짝 쉬어 줍니다.

    Returns:
        (위도, 경도) 또는 None.
    """
    try:
        # Nominatim 은 User-Agent 헤더가 없으면 차단합니다. 반드시 지정.
        headers = {"User-Agent": "family-trip-coordinator-poc/1.0"}
        params = {
            "q": query,
            "format": "json",
            "limit": 1,
            "countrycodes": "kr",  # 한국 결과만
            "accept-language": "ko",
        }
        resp = requests.get(_NOMINATIM_URL, params=params, headers=headers, timeout=5)
        time.sleep(session_delay)  # 호출 정책(초당 1회) 준수
        if resp.status_code == 200:
            data = resp.json()
            if data:
                lat = float(data[0]["lat"])
                lng = float(data[0]["lon"])
                return (lat, lng)
    except Exception:
        # 네트워크/파싱 등 어떤 실패든 폴백으로 넘어감
        pass

    # 폴백: 지역 키워드 기반 근사 좌표
    return _fallback_coords(query)


# ──────────────────────────────────────────────────────────────────────────
# 4) 정확 위치 확인 (핀 정확도용)
#  - geocode_place 는 '실패해도 지역 근처'를 돌려주지만(지도 중심용),
#    핀은 '진짜 그 자리'가 아니면 안 찍는 게 낫습니다(요청 사항).
#  - locate_place 는 "지역 반경 안에서 확실히 찾은 경우"에만 좌표를 돌려주고,
#    아니면 None 을 돌려줍니다. → 상위(build_map_html)에서 None 이면 핀 생략.
# ──────────────────────────────────────────────────────────────────────────
def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    """두 (위도, 경도) 지점 사이의 대략 거리(km). 지역 반경 내 여부 판정에 사용."""
    (lat1, lon1), (lat2, lon2) = a, b
    r = 6371.0  # 지구 반지름(km)
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    h = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(h)))


def geocode_exact(query: str, session_delay: float = 1.0) -> tuple[float, float] | None:
    """Nominatim 으로 지오코딩하되, '실제 결과가 있을 때만' 좌표를 돌려준다(폴백 없음).

    geocode_place 와 달리 실패 시 지역 근사 좌표를 만들어 내지 않습니다.
    → '정확히 못 찾으면 핀을 안 찍는다'는 정책을 위해 사용합니다.
    """
    try:
        headers = {"User-Agent": "family-trip-coordinator-poc/1.0"}
        params = {
            "q": query,
            "format": "json",
            "limit": 1,
            "countrycodes": "kr",
            "accept-language": "ko",
        }
        resp = requests.get(_NOMINATIM_URL, params=params, headers=headers, timeout=5)
        time.sleep(session_delay)  # 초당 1회 정책 준수
        if resp.status_code == 200:
            data = resp.json()
            if data:
                return (float(data[0]["lat"]), float(data[0]["lon"]))
    except Exception:
        pass
    return None


_KAKAO_KEYWORD_URL = "https://dapi.kakao.com/v2/local/search/keyword.json"


def _kakao_lookup(query: str, center: tuple[float, float] | None, radius_m: int) -> dict | None:
    """카카오 로컬 '키워드 검색'. KAKAO_REST_API_KEY 가 있을 때만 동작.

    - center + radius 를 주면 '그 반경 안'의 결과를 거리순으로 돌려줍니다(지역 필터 자동).
    - 반환: {'lat','lng','name','address','place_url','id'} 또는 None.
      · place_url : 카카오맵 '장소 상세' 페이지 URL (영업시간·휴무·메뉴가 담겨 있음)
      · id        : 그 장소의 고유 번호 (상세 정보 JSON 을 부를 때 사용)
    ※ 어떤 경우에도 예외를 던지지 않습니다.
    """
    key = os.getenv("KAKAO_REST_API_KEY")
    if not key:
        return None
    try:
        headers = {"Authorization": f"KakaoAK {key}"}
        params: dict = {"query": query, "size": 5}
        if center is not None:
            params["y"], params["x"] = center[0], center[1]
            params["radius"] = min(int(radius_m), 20000)  # 카카오 최대 20km
            params["sort"] = "distance"
        resp = requests.get(_KAKAO_KEYWORD_URL, headers=headers, params=params, timeout=5)
        if resp.status_code == 200:
            docs = resp.json().get("documents", [])
            if docs:
                d = docs[0]
                return {
                    "lat": float(d["y"]),
                    "lng": float(d["x"]),
                    "name": d.get("place_name", ""),
                    "address": d.get("road_address_name") or d.get("address_name", ""),
                    # ↓ 영업시간/휴무 조회에 쓸 '장소 상세' 링크와 고유 id
                    "place_url": d.get("place_url", ""),
                    "id": str(d.get("id", "")),
                }
    except Exception:
        pass
    return None


def locate_place_info(
    name: str,
    address: str,
    region: str,
    center: tuple[float, float] | None,
    radius_km: float = 3.0,
) -> dict | None:
    """장소를 '지역 중심 반경 안에서 확실히' 찾았을 때만 상세 dict 를 돌려준다.

    반환 dict: {'lat','lng','name','address','place_url','id'}
      - 카카오 경로에서는 place_url/id 가 채워져(영업시간·휴무 조회 가능),
        Nominatim 폴백 경로에서는 좌표만 있고 place_url/id 는 빈 값입니다.
      - 확실히 못 찾으면 None (→ 핀 생략).

    처리 순서:
        1) (KAKAO_REST_API_KEY 있으면) 카카오 로컬 키워드 검색 — 지역 반경으로 필터.
           이름만으로 못 찾으면 '지역+이름'으로 한 번 더 시도.
        2) 카카오 키가 없으면 Nominatim exact → 좌표가 지역 반경 안이면 채택.
        3) 위 모두 실패하면 None.
    """
    radius_m = int(radius_km * 1000)

    # 1) 카카오 (있을 때) — center 반경으로 검색하므로 그 자체가 '지역 안' 보장
    if os.getenv("KAKAO_REST_API_KEY"):
        for q in (name, f"{region} {name}"):
            res = _kakao_lookup(q, center=center, radius_m=radius_m)
            if res:
                return res
        return None  # 카카오로도 지역 반경 안에서 못 찾음 → 핀 없음

    # 2) Nominatim exact (폴백) — 찾더라도 지역 반경 밖이면 '다른 지역'으로 보고 버림
    query = ", ".join(x for x in [name, address, region, "대한민국"] if x)
    coords = geocode_exact(query)
    if coords and center is not None and haversine_km(coords, center) <= radius_km:
        # 폴백 경로에는 카카오 상세 링크가 없으므로 place_url/id 는 빈 값
        return {
            "lat": coords[0],
            "lng": coords[1],
            "name": "",
            "address": address,
            "place_url": "",
            "id": "",
        }
    return None


def locate_place(
    name: str,
    address: str,
    region: str,
    center: tuple[float, float] | None,
    radius_km: float = 3.0,
) -> tuple[float, float] | None:
    """locate_place_info 의 좌표만 필요할 때 쓰는 얇은 래퍼. (핀 좌표용)"""
    info = locate_place_info(name, address, region, center, radius_km)
    return (info["lat"], info["lng"]) if info else None


# ──────────────────────────────────────────────────────────────────────────
# 5) 카카오 '장소 상세'에서 영업시간/휴무 텍스트 긁어오기
#  - 카카오 로컬 검색이 준 place_url/id 로 상세 정보를 가져옵니다.
#  - 카카오맵 웹이 실제로 쓰는 내부 JSON(place-api.map.kakao.com/places/panel3/{id})을 씁니다.
#    이 응답의 open_hours 안에는 '오늘(7/16 목)' 요일이 하이라이트된 주간 영업시간/휴무가 담겨,
#    영업시간·휴무 판단에 딱 맞습니다. (상세 페이지 자체는 SPA라 HTML만으론 제목만 나옴)
#  - 반환은 open_hours 부분을 직렬화한 '가공 최소 텍스트'입니다. 오늘 영업/휴무 판단은
#    상위(nodes.check_open_today)의 LLM 이 맡습니다. (스키마가 바뀌어도 견디도록 파싱 최소화)
#  ※ 이 데이터는 카카오맵 서비스 내부용이므로, 데모/학습 목적 조회로만 사용합니다.
# ──────────────────────────────────────────────────────────────────────────
_KAKAO_PANEL_URL = "https://place-api.map.kakao.com/places/panel3/{id}"
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
)


def _place_id_from_url(place_url: str) -> str:
    """place_url(예: https://place.map.kakao.com/1234567) 끝의 숫자 id 를 뽑아낸다."""
    try:
        tail = (place_url or "").rstrip("/").split("/")[-1]
        return tail if tail.isdigit() else ""
    except Exception:
        return ""


def fetch_place_hours_text(place_url: str, place_id: str = "", max_chars: int = 3500) -> str:
    """카카오 장소 상세에서 '오늘 영업시간/휴무' 판단에 쓸 텍스트를 긁어 온다.

    처리 순서(모두 실패해도 예외 없이 빈 문자열 반환 — 시연 안정성):
        1) 카카오 패널 JSON(place-api.map.kakao.com/places/panel3/{id}) 호출
           → 응답의 open_hours(주간 영업시간·오늘 하이라이트·휴무일)만 추려 직렬화.
             영업시간 정보 자체가 없으면 "" 를 돌려 상위에서 '알 수 없음' 처리하게 함.
        2) 패널 JSON 이 실패하면 place_url HTML 폴백(대개 제목만 나오지만 최후의 보루).
        3) 둘 다 실패하면 빈 문자열.

    Args:
        place_url: 카카오 장소 상세 페이지 URL.
        place_id : 장소 고유 id (없으면 place_url 에서 추출 시도).
        max_chars: LLM 토큰 절약을 위해 잘라낼 최대 길이.

    Returns:
        영업시간 판단에 쓸 텍스트(길이 제한). 없으면 "".
    """
    pid = place_id or _place_id_from_url(place_url)

    # 1) 카카오 패널 JSON (브라우저 유사 헤더가 없으면 406 이 나므로 헤더를 정확히 맞춥니다)
    if pid:
        try:
            headers = {
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "ko-KR",
                "Origin": "https://place.map.kakao.com",
                "Referer": "https://place.map.kakao.com/",
                "Pf": "web",  # 카카오맵 웹 클라이언트 식별용
                "User-Agent": _BROWSER_UA,
            }
            resp = requests.get(_KAKAO_PANEL_URL.format(id=pid), headers=headers, timeout=6)
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, dict):
                    # open_hours 안에 headline(현재상태)·week_from_today(요일별 영업/휴무)가 있습니다.
                    #  오늘 요일은 is_highlight=true 로 표시되어 LLM 이 바로 집어낼 수 있습니다.
                    oh = data.get("open_hours")
                    if oh:
                        blob = json.dumps(oh, ensure_ascii=False)
                        if blob and blob not in ("null", "{}", "[]"):
                            return blob[:max_chars]
                    # 영업시간 정보 자체가 없는 곳(→ 알 수 없음 처리) : LLM 호출을 아끼려 "" 반환
                    return ""
        except Exception:
            pass

    # 2) HTML 폴백 — 태그를 지운 본문 텍스트 (SPA라 제목만 나올 수 있음)
    if place_url:
        try:
            resp = requests.get(place_url, headers={"User-Agent": _BROWSER_UA}, timeout=6)
            if resp.status_code == 200:
                html = resp.text
                html = re.sub(r"<script.*?</script>", " ", html, flags=re.S | re.I)
                html = re.sub(r"<style.*?</style>", " ", html, flags=re.S | re.I)
                text = re.sub(r"<[^>]+>", " ", html)
                text = re.sub(r"\s+", " ", text).strip()
                if text:
                    return text[:max_chars]
        except Exception:
            pass

    return ""
