"""OpenAI API + Streamlit 간단 챗봇 (3.2 OpenAI_API.ipynb 패턴 기반)."""

import copy
import json
import os
from pathlib import Path

import httpx
import streamlit as st
from dotenv import load_dotenv
from openai import OpenAI

APP_DIR = Path(__file__).resolve().parent
ENV_PATH = APP_DIR.parent / ".env"
DEFAULT_SYSTEM = "You are a helpful assistant."
MODEL = "gpt-4o-mini"

# Open-Meteo geocoding은 한글 도시명이 잘 안 잡히는 경우가 있어 별칭을 둡니다.
CITY_ALIASES: dict[str, str] = {
    "서울": "Seoul",
    "부산": "Busan",
    "대구": "Daegu",
    "인천": "Incheon",
    "광주": "Gwangju",
    "대전": "Daejeon",
    "울산": "Ulsan",
    "세종": "Sejong",
    "제주": "Jeju",
}

WEATHER_CODE_KO: dict[int, str] = {
    0: "맑음",
    1: "대체로 맑음",
    2: "부분적으로 흐림",
    3: "흐림",
    45: "안개",
    48: "서리 안개",
    51: "이슬비(약함)",
    53: "이슬비(보통)",
    55: "이슬비(강함)",
    56: "어는 이슬비(약함)",
    57: "어는 이슬비(강함)",
    61: "비(약함)",
    63: "비(보통)",
    65: "비(강함)",
    66: "어는 비(약함)",
    67: "어는 비(강함)",
    71: "눈(약함)",
    73: "눈(보통)",
    75: "눈(강함)",
    77: "싸락눈",
    80: "소나기(약함)",
    81: "소나기(보통)",
    82: "소나기(강함)",
    85: "눈 소나기(약함)",
    86: "눈 소나기(강함)",
    95: "뇌우",
    96: "뇌우(우박 약함)",
    99: "뇌우(우박 강함)",
}


def _geocode_city(city: str) -> list[dict]:
    queries = [city]
    alias = CITY_ALIASES.get(city)
    if alias and alias not in queries:
        queries.append(alias)

    for query in queries:
        geo = httpx.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": query, "count": 1, "language": "ko", "format": "json"},
            timeout=15,
        )
        geo.raise_for_status()
        results = geo.json().get("results") or []
        if results:
            return results
    return []


def get_current_weather(city: str) -> str:
    """현재 날씨 조회(Open-Meteo). Tool 결과는 문자열(JSON)로 반환."""
    city = (city or "").strip()
    if not city:
        return json.dumps({"error": "city가 비어있습니다."}, ensure_ascii=False)

    try:
        results = _geocode_city(city)
        if not results:
            return json.dumps({"error": f"도시를 찾을 수 없습니다: {city}"}, ensure_ascii=False)

        r0 = results[0]
        lat = r0["latitude"]
        lon = r0["longitude"]
        resolved = ", ".join(
            [p for p in [r0.get("name"), r0.get("admin1"), r0.get("country")] if p]
        )

        wx = httpx.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": lat,
                "longitude": lon,
                "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m",
                "timezone": "auto",
            },
            timeout=15,
        )
        wx.raise_for_status()
        wx_data = wx.json()
        cur = wx_data.get("current") or {}

        code = cur.get("weather_code")
        desc = WEATHER_CODE_KO.get(code, f"weather_code={code}")

        return json.dumps(
            {
                "city_query": city,
                "resolved_location": resolved,
                "latitude": lat,
                "longitude": lon,
                "time": cur.get("time"),
                "temperature_c": cur.get("temperature_2m"),
                "apparent_temperature_c": cur.get("apparent_temperature"),
                "wind_speed_10m": cur.get("wind_speed_10m"),
                "description": desc,
                "source": "open-meteo.com",
            },
            ensure_ascii=False,
        )
    except Exception as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_current_weather",
            "description": "도시 이름으로 현재 날씨를 조회합니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {
                        "type": "string",
                        "description": "도시명 (예: 서울, 부산, Tokyo, New York)",
                    }
                },
                "required": ["city"],
            },
        },
    }
]

TOOL_FUNCTIONS = {"get_current_weather": get_current_weather}


@st.cache_resource
def get_client() -> OpenAI:
    load_dotenv(ENV_PATH)
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError(
            f"{ENV_PATH}에 OPENAI_API_KEY=sk-... 를 설정하세요."
        )
    return OpenAI(api_key=api_key)


def chat_completion(
    client: OpenAI,
    messages: list[dict],
    temperature: float,
) -> str:
    api_messages = copy.deepcopy(messages)

    while True:
        response = client.chat.completions.create(
            model=MODEL,
            temperature=temperature,
            messages=api_messages,
            tools=TOOLS,
        )
        msg = response.choices[0].message

        if not msg.tool_calls:
            return msg.content or ""

        api_messages.append(msg.model_dump(exclude_none=True))

        for tc in msg.tool_calls:
            fn_name = tc.function.name
            fn_args = json.loads(tc.function.arguments or "{}")
            fn = TOOL_FUNCTIONS.get(fn_name)
            if not fn:
                result = json.dumps({"error": f"지원하지 않는 tool: {fn_name}"}, ensure_ascii=False)
            else:
                try:
                    result = fn(**fn_args)
                except TypeError as exc:
                    result = json.dumps({"error": f"인자 오류: {exc}"}, ensure_ascii=False)
                except Exception as exc:
                    result = json.dumps({"error": str(exc)}, ensure_ascii=False)

            api_messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                }
            )


def init_session(system_prompt: str) -> None:
    st.session_state.messages = [
        {"role": "system", "content": system_prompt},
    ]


def main() -> None:
    st.set_page_config(page_title="OpenAI 챗봇", page_icon="💬", layout="centered")
    st.title("💬 OpenAI 챗봇")
    st.caption("3.2 OpenAI_API.ipynb · `chat.completions.create()` 멀티턴 대화")

    with st.sidebar:
        st.header("설정")
        system_prompt = st.text_area(
            "시스템 프롬프트",
            value=DEFAULT_SYSTEM,
            height=100,
        )
        temperature = st.slider("temperature", 0.0, 1.0, 0.2, 0.1)
        if st.button("대화 초기화", use_container_width=True):
            init_session(system_prompt)
            st.rerun()

    if "messages" not in st.session_state:
        init_session(system_prompt)
    elif st.session_state.messages[0]["content"] != system_prompt:
        st.session_state.messages[0] = {"role": "system", "content": system_prompt}

    try:
        client = get_client()
    except ValueError as exc:
        st.error(str(exc))
        st.stop()

    for message in st.session_state.messages:
        if message["role"] in ("system", "tool"):
            continue
        content = message.get("content")
        if not content:
            continue
        with st.chat_message(message["role"]):
            st.markdown(content)

    if prompt := st.chat_input("메시지를 입력하세요"):
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        with st.chat_message("assistant"):
            with st.spinner("생각 중..."):
                try:
                    reply = chat_completion(
                        client,
                        st.session_state.messages,
                        temperature,
                    )
                except Exception as exc:
                    st.error(f"API 오류: {exc}")
                    st.session_state.messages.pop()
                    st.stop()
            st.markdown(reply)

        st.session_state.messages.append({"role": "assistant", "content": reply})


if __name__ == "__main__":
    main()
