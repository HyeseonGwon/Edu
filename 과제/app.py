"""
app.py  (Frontend / Streamlit)
==============================
사용자가 실제로 보는 화면입니다. '상단=결과 / 하단=대화' 구조로 배치합니다.
    - 상단(추천 결과): 왼쪽 Folium 지도 + 오른쪽 업체 리스트(스크롤)
    - 하단(대화창)   : 가로로 넓은 대화 영역 + 맨 아래 고정 입력창
    - 왼쪽 사이드바  : 설정·도움말 (Streamlit 기본 '<<' 컨트롤로 접기 가능)

동작 방식 (발표 포인트)
----------------------
- UI 는 로직을 갖지 않고, FastAPI(main.py)의 엔드포인트만 호출합니다.
  → '화면(app.py)'과 '두뇌(main.py+LangGraph)'가 깔끔히 분리됩니다.
- 대화로 3가지(누구와/무엇을/어디로)가 모이면(stage='ready'),
  후보를 '한 개씩' 검색·검증하며 조건을 모두 통과한 곳을 '바로바로' 목록에 추가합니다.
- 조건 통과 5곳을 채우거나 후보가 소진되거나 '그만 찾기'를 누르면 멈춥니다.
- 서버가 만들어 준 Folium 지도 HTML 을 st.components.v1.html 로 그대로 렌더링합니다.

실행 방법
--------
    # 터미널 1 (백엔드)
    uvicorn main:app --reload --port 8000 
    # 터미널 2 (프런트)
    streamlit run app.py
"""

from __future__ import annotations

import base64
import json
import os

import requests
import streamlit as st
import streamlit.components.v1 as components


@st.cache_data(show_spinner=False)
def _bg_data_uri(path: str) -> str | None:
    """배경 이미지 파일을 data URI(base64)로 인코딩한다. 없으면 None.

    - 매 재실행마다 파일을 다시 읽고 인코딩하지 않도록 캐시한다.
    - 확장자에 맞춰 MIME 타입을 정한다(png/jpg/webp).
    """
    if not os.path.exists(path):
        return None
    ext = os.path.splitext(path)[1].lower()
    mime = (
        "image/jpeg" if ext in (".jpg", ".jpeg")
        else "image/webp" if ext == ".webp"
        else "image/png"
    )
    with open(path, "rb") as f:
        return f"data:{mime};base64," + base64.b64encode(f.read()).decode()

# ──────────────────────────────────────────────────────────────────────────
# 페이지 기본 설정 (넓은 레이아웃이어야 좌우 분할이 시원하게 보입니다)
#  - 사이드바(설정/도움말)는 Streamlit 기본 '<<' 컨트롤로 접거나 펼칠 수 있습니다.
#  ★ st.set_page_config 는 반드시 '첫 번째 Streamlit 명령'이어야 합니다.
#    (그래서 아래 CSS 주입(st.markdown)은 이 호출 뒤에 둡니다.)
# ──────────────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="대가족 나들이 코디네이터",
    page_icon="👪",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ──────────────────────────────────────────────────────────────────────────
# 배경 CSS 주입 — 해변을 떠올리게 하는 시원한 여름 감성 그라디언트 배경.
#  (반드시 set_page_config 뒤에서 호출해야 합니다.)
# ──────────────────────────────────────────────────────────────────────────
page_bg_img = """
<style>
/* 전체 앱 배경 설정 — 앱 전체를 감싸는 안정적인 testid 에 직접 적용 */
[data-testid="stAppViewContainer"] {
    background-image: linear-gradient(180deg, #FDFCFB 0%, #E2D1C3 30%, #4FACFE 70%, #00F2FE 100%);
    background-attachment: fixed;
    background-size: cover;
}

/* 상단 헤더(툴바)를 투명 처리해 배경 그라디언트가 위까지 이어져 보이게 함 */
[data-testid="stHeader"] {
    background: rgba(0, 0, 0, 0);
}

/* 사이드바 배경 설정 (옵션: 사이드바는 조금 더 차분하게) */
[data-testid="stSidebar"] > div:first-child {
    background-image: linear-gradient(180deg, #EDF2F4 0%, #CED4DA 100%);
    background-attachment: fixed;
    background-size: cover;
}

/* 콘텐츠 컨테이너(결과 리스트·대화창)는 '통째로 불투명한 흰색'으로 처리 →
   중요한 추천/대화 정보가 배경에 묻히지 않도록 확실히 띄운다.
   (이 앱에서 테두리 컨테이너는 최종 추천 박스와 대화 박스 두 곳뿐이다) */
[data-testid="stVerticalBlockBorderWrapper"] {
    background: #ffffff;
    border: 1px solid rgba(18, 48, 61, 0.12);
    border-radius: 14px;
    box-shadow: 0 6px 20px rgba(18, 48, 61, 0.14);
}

/* 버튼('다시 찾아보기'·'그만 찾기'·'새 여행 계획 시작' 등):
   반투명 흰 배경 + '진한 회색' 글자/테두리로 가독성 확보.
   (primary 버튼의 흰 글자가 안 보이던 문제도 글자색을 진하게 잡아 해결) */
.stButton > button {
    background: rgba(255, 255, 255, 0.92) !important;
    color: #1f2d34 !important;
    border: 1px solid rgba(18, 48, 61, 0.35) !important;
    font-weight: 600;
    backdrop-filter: blur(6px);
    -webkit-backdrop-filter: blur(6px);
}
/* 비활성 버튼도 완전히 흐려지지 않게 중간 회색으로 (가독성 유지) */
.stButton > button:disabled {
    background: rgba(255, 255, 255, 0.70) !important;
    color: #55636b !important;
    border: 1px solid rgba(18, 48, 61, 0.20) !important;
}

/* 검색 반경 슬라이더: 라벨/눈금/현재값 글자를 진한 회색으로
   (그라디언트 위에 바로 놓여 기본 연회색 글자가 잘 안 보이던 문제 해결) */
.stSlider [data-testid="stWidgetLabel"] p,
[data-testid="stSliderTickBarMin"],
[data-testid="stSliderTickBarMax"],
[data-testid="stThumbValue"] {
    color: #1f2d34 !important;
    font-weight: 600;
}

/* 하단 채팅 입력 '바' 전체는 투명 처리 → 배경 그라디언트가 끊김 없이 이어지게 */
[data-testid="stBottom"],
[data-testid="stBottom"] > div,
[data-testid="stBottomBlockContainer"] {
    background: transparent !important;
}

/* 실제 입력 필드(pill)만 반투명 흰 배경으로 살짝 띄워 가독성 확보 */
[data-testid="stChatInput"] {
    background: rgba(255, 255, 255, 0.85);
    border-radius: 12px;
    border: 1px solid rgba(255, 255, 255, 0.55);
    backdrop-filter: blur(6px);
    -webkit-backdrop-filter: blur(6px);
}
</style>
"""
st.markdown(page_bg_img, unsafe_allow_html=True)

# ──────────────────────────────────────────────────────────────────────────
# 배경 '사진' 얹기 — summer_bg.png 가 있으면 위 그라디언트 대신 사진을 배경으로.
#  - 파일이 없으면 위 그라디언트가 그대로 폴백으로 유지됩니다.
#  - 나중에 선언된 규칙이라 그라디언트 background-image 를 덮어씁니다.
# ──────────────────────────────────────────────────────────────────────────
_bg_uri = _bg_data_uri(os.path.join(os.path.dirname(__file__), "summer_bg.png"))
if _bg_uri:
    st.markdown(
        f"""
<style>
[data-testid="stAppViewContainer"] {{
    background-image: url("{_bg_uri}");
    background-size: cover;
    background-position: center;
    background-attachment: fixed;
}}
</style>
""",
        unsafe_allow_html=True,
    )

DEFAULT_API = "http://127.0.0.1:8000"
TARGET_FINALISTS = 5  # 목표 최종 후보 수(서버의 TARGET_FINALISTS 와 맞춤)
# 지도·리스트 높이 정렬
#  - MAP_HEIGHT: Folium 지도 iframe 높이
#  - SLIDER_ROW_HEIGHT: 지도 위 '반경 슬라이더+다시찾기' 행의 대략 높이
#  - LIST_TITLE_HEIGHT: 리스트 위 '최종 추천 N곳' 제목 대략 높이
#  → 리스트 박스 = 지도 + 슬라이더행 − 제목  → 좌우 하단이 맞춰짐
MAP_HEIGHT = 500
SLIDER_ROW_HEIGHT = 88
LIST_TITLE_HEIGHT = 28
LIST_HEIGHT = MAP_HEIGHT + SLIDER_ROW_HEIGHT - LIST_TITLE_HEIGHT  # ≈ 560


# ──────────────────────────────────────────────────────────────────────────
# 세션 상태 초기화 (Streamlit 은 위젯 상호작용마다 스크립트를 재실행하므로,
#  대화·지도 등은 st.session_state 에 보관해 유지해야 합니다.)
# ──────────────────────────────────────────────────────────────────────────
def _init_state():
    ss = st.session_state
    ss.setdefault("api_base", DEFAULT_API)
    ss.setdefault("session_id", None)       # 서버가 발급하는 세션 ID
    ss.setdefault("messages", [])           # [{'role','content'}] 형태의 대화 로그
    ss.setdefault("map_html", "")           # 우측 지도 HTML
    ss.setdefault("finalists", [])          # 최종 추천 리스트(조건 모두 통과)
    ss.setdefault("requirements", None)     # 현재까지 파악된 조건
    ss.setdefault("stage", "collecting")    # 진행 단계

    # ── 후보 '한 개씩' 검색 진행 상태 ──
    ss.setdefault("search_active", False)   # 검색 루프 진행 중인지
    ss.setdefault("search_stop", False)     # '그만 찾기' 요청 여부(버튼 콜백이 설정)
    ss.setdefault("search_step_idx", 0)     # 지금까지 실행한 스텝(후보 검증) 횟수
    ss.setdefault("search_radius_km", 5)    # 검색 영역 반경(km) 기본값 — 도시 내 기준 5km


_init_state()


# ──────────────────────────────────────────────────────────────────────────
# 서버 통신 헬퍼
# ──────────────────────────────────────────────────────────────────────────
@st.cache_data(ttl=15, show_spinner=False)
def fetch_health(api_base: str) -> dict | None:
    """백엔드 /health 를 호출해 상태(dict)를 돌려준다. (연결 실패 시 None)

    - Kakao Local API 연결 여부(kakao) 등을 사이드바 배너로 보여주는 데 씁니다.
    - Streamlit 은 재실행이 잦아 매번 호출하면 낭비이므로 짧게 캐시(ttl=15s)합니다.
      (api_base 가 바뀌면 캐시 키가 달라져 자동으로 다시 조회합니다.)
    """
    try:
        r = requests.get(f"{api_base}/health", timeout=5)
        if r.status_code == 200:
            return r.json()
    except Exception:
        return None
    return None


def call_chat(message: str) -> dict | None:
    """(비스트리밍 대체용) FastAPI /chat 을 한 번에 호출해 응답(dict)을 돌려준다.

    기본 UI는 handle_turn() 의 '스트리밍(/chat/stream)' 을 사용합니다.
    이 함수는 스트리밍이 불가한 환경에서의 폴백/디버깅용으로 남겨 둡니다.
    """
    try:
        resp = requests.post(
            f"{st.session_state.api_base}/chat",
            json={"message": message, "session_id": st.session_state.session_id},
            timeout=120,
        )
        if resp.status_code == 200:
            return resp.json()
        st.error(f"서버 오류: {resp.status_code}")
    except requests.exceptions.ConnectionError:
        st.error("백엔드에 연결할 수 없습니다. `uvicorn main:app --port 8000` 이 실행 중인지 확인하세요.")
    except Exception as e:
        st.error(f"요청 실패: {e}")
    return None


def handle_turn(prompt: str):
    """사용자 입력 1턴을 '스트리밍'으로 처리한다.

    /chat/stream 을 호출해 서버가 흘려보내는 진행 문구를 st.status 박스에 실시간으로
    갱신해 보여줍니다. 맨 마지막 'final' 이벤트가 오면 대화/조건을 세션 상태에 반영하고,
    3가지 정보가 모두 모였으면(stage='ready') 후보 검색 루프를 시작합니다.
    """
    ss = st.session_state
    ss.messages.append({"role": "user", "content": prompt})

    final = None  # 최종 결과 이벤트를 담을 변수

    # st.status: 스피너 + 접을 수 있는 로그 박스 (진행 상황을 실시간으로 표시)
    with st.status("에이전트가 입력을 이해하는 중이에요...", expanded=True) as status:
        try:
            with requests.post(
                f"{ss.api_base}/chat/stream",
                json={"message": prompt, "session_id": ss.session_id},
                stream=True,          # 응답을 통째로 받지 않고 '흘러오는 대로' 읽음
                timeout=120,
            ) as resp:
                resp.encoding = "utf-8"  # 한글 진행 문구가 깨지지 않도록
                if resp.status_code != 200:
                    status.update(label=f"서버 오류: {resp.status_code}", state="error")
                else:
                    for line in resp.iter_lines(decode_unicode=True):
                        if not line:
                            continue
                        try:
                            evt = json.loads(line)
                        except Exception:
                            continue
                        if evt.get("type") == "progress":
                            msg = evt.get("message", "")
                            status.update(label=msg)
                            st.write(f"⏳ {msg}")
                        elif evt.get("type") == "final":
                            final = evt
                    status.update(label="완료!", state="complete")
        except requests.exceptions.ConnectionError:
            status.update(
                label="백엔드에 연결할 수 없어요. `uvicorn main:app --port 8000` 이 실행 중인지 확인하세요.",
                state="error",
            )
        except Exception as e:
            status.update(label=f"요청 실패: {e}", state="error")

    if final is None:
        ss.messages.append(
            {"role": "assistant", "content": "죄송해요, 서버 응답을 받지 못했어요."}
        )
        st.rerun()
        return

    # 서버가 발급/유지하는 세션 ID 저장 (다음 턴에 그대로 전달 → 멀티턴 유지)
    ss.session_id = final.get("session_id")
    ss.stage = final.get("stage", "collecting")
    ss.requirements = final.get("requirements")
    ss.messages.append({"role": "assistant", "content": final.get("assistant_message", "")})

    # ── 정보가 다 모이면(stage='ready') 후보를 '한 개씩' 찾는 검색 루프를 시작 ──
    #  실제 검색은 아래 run_search_step() 이 /search/step 을 반복 호출하며 수행합니다.
    if ss.stage == "ready":
        ss.search_active = True
        ss.search_stop = False
        ss.search_step_idx = 0
        ss.finalists = []   # 새 검색이므로 이전 결과 초기화
        ss.map_html = ""

    # 처리가 끝났으니 화면을 다시 그려 상단 결과 패널·대화창을 최신 상태로 갱신합니다.
    st.rerun()


# ──────────────────────────────────────────────────────────────────────────
# 후보 '한 개씩' 검색 루프 (핵심 기능)
#  - /search/step 을 호출해 후보 한 개를 검증하고, 조건을 모두 통과하면 즉시 목록에 추가.
#  - 한 스텝을 마칠 때마다 st.rerun() 으로 화면을 갱신해 추천 목록이 '바로바로' 늘어납니다.
#  - 5곳을 채우거나 후보가 소진되거나 '그만 찾기'를 누르면 멈춥니다.
#  - Streamlit 은 블로킹 요청 중 버튼을 처리하지 못하므로, '한 번에 한 스텝'만 실행하고
#    다음 스텝 사이에 '그만 찾기' 버튼 입력을 받아 루프를 멈출 수 있게 합니다.
# ──────────────────────────────────────────────────────────────────────────
def _request_stop():
    """'그만 찾기' 버튼 콜백 — 다음 스텝 시작 전에 감지되어 루프를 멈춥니다."""
    st.session_state.search_stop = True


def _restart_search():
    """'다시 찾아보기' 버튼 콜백 — 현재 반경으로 검색을 처음부터 다시 실행한다.

    지역에 결과가 드물 때 반경을 넓혀 재검색하는 용도. 조건(requirements)은 그대로 두고
    누적 결과만 비운 뒤 검색 루프를 재시작합니다. (첫 스텝이 reset=True 로 서버 누적도 초기화)
    """
    ss = st.session_state
    if not ss.session_id or not ss.requirements:
        return  # 아직 검색할 조건이 없으면 무시
    ss.search_active = True
    ss.search_stop = False
    ss.search_step_idx = 0
    ss.finalists = []
    ss.map_html = ""


def _call_step(reset: bool, status) -> dict | None:
    """/search/step 을 스트리밍 호출해 진행 문구를 status 에 실시간 표시하고 결과를 반환."""
    ss = st.session_state
    final = None
    try:
        with requests.post(
            f"{ss.api_base}/search/step",
            json={
                "session_id": ss.session_id,
                "reset": reset,
                "radius_km": ss.search_radius_km,
            },
            stream=True,
            timeout=180,
        ) as resp:
            resp.encoding = "utf-8"
            if resp.status_code != 200:
                status.update(label=f"서버 오류: {resp.status_code}", state="error")
                return None
            for line in resp.iter_lines(decode_unicode=True):
                if not line:
                    continue
                try:
                    evt = json.loads(line)
                except Exception:
                    continue
                if evt.get("type") == "progress":
                    m = evt.get("message", "")
                    status.update(label=m)
                    status.write(f"⏳ {m}")
                elif evt.get("type") == "final":
                    final = evt
            status.update(label="한 곳 확인 완료", state="complete")
    except requests.exceptions.ConnectionError:
        status.update(label="백엔드에 연결할 수 없어요.", state="error")
        return None
    except Exception as e:
        status.update(label=f"요청 실패: {e}", state="error")
        return None
    return final


def _finish_search(stopped: bool):
    """검색 루프 종료: /search/finish 로 최종 안내문을 받아 대화에 남기고 루프를 끝낸다."""
    ss = st.session_state
    ss.search_active = False
    ss.search_stop = False
    try:
        r = requests.post(
            f"{ss.api_base}/search/finish",
            json={"session_id": ss.session_id, "stopped": stopped},
            timeout=60,
        )
        if r.status_code == 200:
            data = r.json()
            if data.get("finalists") is not None:
                ss.finalists = data["finalists"]
            if data.get("map_html"):
                ss.map_html = data["map_html"]
            msg = data.get("assistant_message", "")
            if msg:
                ss.messages.append({"role": "assistant", "content": msg})
    except Exception as e:
        ss.messages.append({"role": "assistant", "content": f"검색을 마무리했어요. (요약 중 오류: {e})"})
    ss.stage = "done"
    st.rerun()


def run_search_step(panel):
    """후보 한 개를 검증하고, 계속할지/멈출지 결정한다. (panel: 진행 UI 를 그릴 컨테이너)"""
    ss = st.session_state
    with panel.container():
        st.markdown("#### 🔎 조건에 맞는 곳을 하나씩 찾는 중")
        left, right = st.columns([2, 1])
        left.progress(
            min(len(ss.finalists) / TARGET_FINALISTS, 1.0),
            text=f"조건을 모두 통과한 곳 {len(ss.finalists)} / {TARGET_FINALISTS}곳 확보",
        )
        # '그만 찾기' 버튼: 콜백으로 search_stop 을 세팅 (블로킹 중 클릭도 다음 실행에서 감지됨)
        right.button("⏹ 그만 찾기", on_click=_request_stop, use_container_width=True, type="primary")

        # 직전 실행에서 '그만 찾기'가 눌렸다면 여기서 즉시 종료
        if ss.search_stop:
            _finish_search(stopped=True)
            return

        status = st.status("후보 한 곳을 확인하는 중...", expanded=True)

    # 첫 스텝이면 서버 누적을 초기화(reset=True)하고 새로 시작
    result = _call_step(reset=(ss.search_step_idx == 0), status=status)
    if result is None:  # 오류 → 지금까지 결과로 마무리
        _finish_search(stopped=False)
        return

    # 누적 결과 반영 (지도/최종 후보는 서버가 세션에 누적한 것을 그대로 사용)
    ss.finalists = result.get("finalists", ss.finalists)
    if result.get("map_html"):
        ss.map_html = result["map_html"]
    ss.search_step_idx += 1

    done = result.get("done")            # 5곳 채움
    exhausted = result.get("exhausted")  # 더 이상 새 후보 없음
    reached_max = result.get("reached_max")  # 안전 상한 도달

    if ss.search_stop or done or exhausted or reached_max:
        _finish_search(stopped=bool(ss.search_stop) and not done)
        return

    # 아직 5곳을 못 채웠고 멈춤 요청도 없음 → 다음 후보로 자동 진행
    st.rerun()


def reset_session():
    """대화·지도·결과를 모두 초기화하고 서버 세션도 지운다."""
    ss = st.session_state
    if ss.session_id:
        try:
            requests.post(
                f"{ss.api_base}/reset",
                json={"message": "", "session_id": ss.session_id},
                timeout=10,
            )
        except Exception:
            pass
    ss.session_id = None
    ss.messages = []
    ss.map_html = ""
    ss.finalists = []
    ss.requirements = None
    ss.stage = "collecting"
    ss.search_active = False
    ss.search_stop = False
    ss.search_step_idx = 0


def render_finalist_list():
    """오른쪽 결과 리스트(스크롤 영역)를 그린다. 지도 핀과 동일한 검증 정보를 카드로 표시.

    ※ '요구한 조건'만 표시합니다. (예: 안매운 메뉴만 요청했다면 계단 항목은 숨김)
    ※ 각 조건에는 정보를 확인한 출처로 이동하는 '자세히 보기' 링크를 답니다.
    """
    ss = st.session_state
    finalists = ss.finalists
    req = ss.requirements or {}
    need_stairs = bool(req.get("need_no_stairs"))
    need_menu = bool(req.get("need_kid_friendly"))
    if finalists:
        st.markdown(f"**📍 최종 추천 {len(finalists)}곳**")
    else:
        st.markdown("**📍 최종 추천**")
    # 지도(MAP_HEIGHT) + 위쪽 슬라이더 행을 반영한 LIST_HEIGHT.
    #  (제목은 이미 그렸으므로 LIST_HEIGHT 에 제목 분을 빼 두어 하단이 지도와 맞춤)
    list_box = st.container(height=LIST_HEIGHT, border=True)
    with list_box:
        if not finalists:
            st.caption("조건을 모두 통과한 곳이 여기에 하나씩 추가됩니다. 아래 대화창에서 조건을 알려주세요.")
        status_map = {"yes": "충족 ✅", "no": "미충족 ❌", "unknown": "확인필요 ❓"}
        for i, p in enumerate(finalists, 1):
            # 제목엔 추가 정보 뱃지만: 메뉴 일치 · 오늘 영업중
            #  (통과·지도표시는 리스트=통과 결과, 핀=위치 확실할 때만 찍히므로 생략)
            badges: list[str] = []
            if p.get("menu_match"):
                badges.append("🍽️ 메뉴 일치")
            if p.get("open_today") == "open":
                badges.append("🟢 오늘 영업중!")
            badge_txt = ("  ·  " + "  ·  ".join(badges)) if badges else ""
            with st.expander(
                f"{i}. {p.get('name','(이름 미상)')}{badge_txt}",
                expanded=(i == 1),
            ):
                st.write(f"**분류**: {p.get('category') or '장소'}")
                # 주소는 카카오 도로명 주소를 우선 채우므로, 있으면 항상 보여 줌
                if p.get("address"):
                    st.write(f"**주소**: {p['address']}")
                else:
                    st.caption("↳ 주소 정보를 확인하지 못했어요.")
                # 카카오 장소 상세 링크 — 식당/숙소 공통 (검증 조건과 무관하게 항상 표시)
                if p.get("place_url"):
                    st.markdown(f"[🗺️ 카카오맵에서 보기]({p['place_url']})")
                if p.get("menu_match") and req.get("menu"):
                    st.write(f"**메뉴**: '{req.get('menu')}' 취급 언급 있음 🍽️")
                # 오늘 영업 여부(추가 정보): 영업중이면 시간까지, 미확인이면 안내만
                if p.get("open_today") == "open":
                    hours_txt = p.get("today_hours") or ""
                    st.write("**오늘 영업**: 영업중 🟢" + (f"  ·  {hours_txt}" if hours_txt else ""))
                elif p.get("open_today") == "unknown":
                    st.caption("↳ 오늘 영업 여부는 확인하지 못했어요. 방문 전 확인을 권해요.")
                if not p.get("located"):
                    st.caption("↳ 정확한 좌표를 확인하지 못해 지도에는 핀을 표시하지 않았어요.")

                # 요구한 조건만 노출 (요구하지 않은 항목·숙소의 메뉴 조건은 표시하지 않음)
                cat = p.get("category") or ""
                is_lodge = any(
                    k in cat
                    for k in ("숙소", "호텔", "펜션", "모텔", "게스트하우스", "리조트", "민박")
                )
                show_menu = need_menu and not is_lodge
                if need_stairs:
                    st.write(f"**적은 계단**: {status_map.get(p.get('stair_status'), '확인필요 ❓')}")
                    if p.get("stair_note"):
                        st.caption(f"↳ {p['stair_note']}")
                    if p.get("stair_source"):
                        st.markdown(f"[🔗 자세히 보기]({p['stair_source']})")
                if show_menu:
                    st.write(f"**어린이/안매운 메뉴**: {status_map.get(p.get('menu_status'), '확인필요 ❓')}")
                    if p.get("menu_note"):
                        st.caption(f"↳ {p['menu_note']}")
                    if p.get("menu_source"):
                        st.markdown(f"[🔗 자세히 보기]({p['menu_source']})")
                elif need_menu and is_lodge and p.get("menu_note"):
                    st.caption(f"↳ {p['menu_note']}")
                if not need_stairs and not show_menu and not (need_menu and is_lodge):
                    st.caption("요청하신 별도 검증 조건이 없어요.")


# ──────────────────────────────────────────────────────────────────────────
# 사이드바 : 설정 · 사용법 · 초기화 (좌측 상단 '<<' 로 접을 수 있음)
# ──────────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ 설정")
    st.session_state.api_base = st.text_input("백엔드 API 주소", value=st.session_state.api_base)

    # ── Kakao Map API 연결 상태 배너 ──
    #  백엔드 /health 가 알려 주는 kakao 값으로, 지도 정확도·폐업/휴무 필터 동작 여부를 안내.
    _health = fetch_health(st.session_state.api_base)
    if _health is None:
        st.error("🔴 백엔드에 연결할 수 없어요. 위 주소를 확인하거나 서버를 실행해 주세요.")
    elif _health.get("kakao"):
        st.success(
            "🟢 **Kakao Map API 연결됨**\n\n"
            "정확한 위치에 핀을 찍고, **폐업·오늘 휴무 업체는 자동으로 제외**돼요."
        )
    else:
        st.warning(
            "🟡 **Kakao Map API 미연결**\n\n"
            "정확한 위치에 핀이 표시되지 않을 수 있고, "
            "**폐업했거나 오늘 휴무인 업체가 결과에 포함**될 수 있어요.\n\n"
            "`실습/.env` 에 `KAKAO_REST_API_KEY` 를 넣고 서버를 재시작하면 정확도가 크게 올라갑니다."
        )

    if st.button("🔄 새 여행 계획 시작", use_container_width=True):
        reset_session()
        st.rerun()

    st.divider()
    st.subheader("💡 이렇게 물어보세요")
    st.caption(
        "- 부모님·아기와 함께 서울 송파구에서 점심 먹을 식당 찾아줘\n"
        "- 휠체어 타는 할머니랑 강남에서 안 매운 밥집\n"
        "- 유모차 끌고 갈 수 있는 서울 성수동 카페 추천"
    )
    st.divider()
    st.caption("Streamlit · FastAPI · LangGraph · Folium · Kakao Local · Nominatim · DuckDuckGo.")


# ──────────────────────────────────────────────────────────────────────────
# 상단 헤더: 타이틀 (사이드바는 좌측 상단의 기본 '<<' 컨트롤로 접을 수 있어요)
# ──────────────────────────────────────────────────────────────────────────
st.title("👪 대가족 나들이 코디네이터")
st.caption("대화로 조건을 알려주시면, 계단 여부·아이 메뉴까지 검증해 조건을 모두 통과한 곳만 지도에 추천해요.")

# 검색 진행 UI 를 그릴 자리(placeholder). 실제 실행은 화면 맨 아래에서 합니다.
search_panel = st.empty()

# ══════════════════════════════════════════════════════════════════════════
# [상단] 추천 결과 — 왼쪽: 지도(+반경 컨트롤) / 오른쪽: 업체 리스트(스크롤)
# ══════════════════════════════════════════════════════════════════════════
st.subheader("🗺️ 추천 결과")

# 조건 수집 현황을 한 줄로 요약 (공간 절약)
req = st.session_state.requirements
if req:
    parts = [
        f"**누구와** {req.get('companions') or '미정'}",
        f"**무엇을** {req.get('place_type') or '미정'}",
        f"**어디로** {req.get('region') or '미정'}",
    ]
    # 메뉴는 선택 조건 — 있을 때만 표시
    if req.get("menu"):
        parts.append(f"**메뉴** {req.get('menu')}")
    line = "　·　".join(parts)
    flags = []
    if req.get("need_no_stairs"):
        flags.append("♿ 계단 적은 곳")
    if req.get("need_kid_friendly"):
        flags.append("🍚 아이·안매운 메뉴")
    if flags:
        line += "　·　검증조건: " + " / ".join(flags)
    st.caption(line)

col_map, col_list = st.columns([3, 2], gap="large")

# ── 왼쪽: 검색 반경 컨트롤 + Folium 지도 ──
#  슬라이더·다시찾기는 '지도의 검색 영역'을 조절하는 컨트롤이므로 지도 바로 위에 둡니다.
with col_map:
    radius_col, btn_col = st.columns([3, 2], gap="small", vertical_alignment="bottom")
    with radius_col:
        st.session_state.search_radius_km = st.slider(
            "🔍 검색 반경 (km)",
            min_value=1,
            max_value=20,
            value=int(st.session_state.search_radius_km),
            step=1,
            help="지역에 결과가 드물면 반경을 넓힌 뒤 '다시 찾아보기'를 눌러 주세요. (최대 20km)",
            disabled=st.session_state.search_active,
        )
    with btn_col:
        st.button(
            "🔎 다시 찾아보기",
            on_click=_restart_search,
            use_container_width=True,
            disabled=(
                st.session_state.search_active
                or not st.session_state.session_id
                or not st.session_state.requirements
            ),
            help="현재 반경으로 후보를 처음부터 다시 찾습니다.",
        )

    if st.session_state.map_html:
        components.html(st.session_state.map_html, height=MAP_HEIGHT, scrolling=False)
        st.caption(
            "🔵 파란 원 = 검색 영역 ·  📍 초록 핀 = 정확한 위치가 확인된 곳  "
        )
    else:
        # 지도 없을 때도 슬라이더 아래 영역을 지도와 같은 높이로 유지
        with st.container(height=MAP_HEIGHT, border=True):
            st.info("아직 지도가 없어요. 아래 대화창에서 조건을 알려주시면 지도가 나타납니다.")

# ── 오른쪽: 업체 리스트(스크롤) ──
with col_list:
    render_finalist_list()

# ══════════════════════════════════════════════════════════════════════════
# [하단] 대화창 — 가로로 넓게. 조사 결과는 위 리스트에 있으므로 여기선 대화만.
# ══════════════════════════════════════════════════════════════════════════
st.divider()
st.subheader("💬 대화")
chat_box = st.container(height=260, border=True)
with chat_box:
    if not st.session_state.messages:
        st.chat_message("assistant").write(
            "안녕하세요! 다양한 가족과 함께하는 여정을 도와드릴게요.\n\n"
            "**① 누구와 · ② 무엇을(식당) · ③ 어디로** 를 알려주시면, "
            "조건을 모두 통과한 곳을 하나씩 찾아 지도에 올려 드려요."
        )
    for m in st.session_state.messages:
        st.chat_message(m["role"]).write(m["content"])

# ──────────────────────────────────────────────────────────────────────────
# 사용자 입력 (st.chat_input 은 항상 화면 맨 아래에 고정됩니다.)
#  - 처리는 대화창 바로 아래에서 하여, 진행 상태(status)가 대화 흐름과 이어져 보입니다.
#  - handle_turn 은 처리 후 st.rerun() 하여 위쪽 결과 패널까지 새로고침합니다.
# ──────────────────────────────────────────────────────────────────────────
prompt = st.chat_input(
    "조건에 맞는 곳을 찾는 중이에요. '그만 찾기'로 멈출 수 있어요." if st.session_state.search_active
    else "예) 부모님과 아기 데리고 서울 송파구에서 점심 먹을 곳 찾아줘",
    disabled=st.session_state.search_active,  # 검색 중에는 새 입력 잠금
)
if prompt and not st.session_state.search_active:
    handle_turn(prompt)

# ──────────────────────────────────────────────────────────────────────────
# 검색 스텝 실행 (화면을 다 그린 뒤 맨 아래에서 한 스텝만 실행)
#  - 위 결과 패널에 '지금까지 누적된' 지도·리스트가 먼저 렌더링된 상태에서 다음 후보를 확인합니다.
#  - run_search_step 은 한 후보를 확인하면 st.rerun() 으로 다음 후보를 이어가며,
#    그 사이에 '그만 찾기' 버튼 입력을 받아 루프를 멈출 수 있게 합니다.
# ──────────────────────────────────────────────────────────────────────────
if st.session_state.search_active:
    run_search_step(search_panel)
