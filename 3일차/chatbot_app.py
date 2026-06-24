"""OpenAI API + Streamlit 간단 챗봇 (3.2 OpenAI_API.ipynb 패턴 기반)."""

import os
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv
from openai import OpenAI

APP_DIR = Path(__file__).resolve().parent
ENV_PATH = APP_DIR.parent / ".env"
DEFAULT_SYSTEM = "You are a helpful assistant."
MODEL = "gpt-4o-mini"


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
    response = client.chat.completions.create(
        model=MODEL,
        temperature=temperature,
        messages=messages,
    )
    return response.choices[0].message.content


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
        if message["role"] == "system":
            continue
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

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
