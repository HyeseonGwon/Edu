import os  # 파일 경로 처리를 위한 표준 라이브러리
from pathlib import Path  # .env 경로 계산용 (이 파일 기준 경로 잡기)

import pymupdf  # PDF에서 텍스트를 추출하는 라이브러리 (fitz)
from dotenv import load_dotenv  # .env 파일에서 환경변수(API 키)를 불러오기 위한 함수
from langchain_core.messages import HumanMessage, SystemMessage  # LangChain 메시지 타입
from langchain_core.tools import tool  # 함수를 LangChain 도구로 등록하는 데코레이터
from langchain_openai import ChatOpenAI  # OpenAI 모델을 LangChain에서 쓰는 래퍼 클래스

APP_DIR = Path(__file__).resolve().parent  # 이 파이썬 파일이 있는 폴더(5일차/)
ENV_PATH = APP_DIR.parent / ".env"  # 프로젝트 루트(실습/)의 .env 경로
load_dotenv(ENV_PATH)  # .env를 읽어 OPENAI_API_KEY를 환경변수로 등록

llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.1)  # 답변 생성에 사용할 LLM 인스턴스 (OpenAI 직접 호출 대신 사용)


@tool  # 이 함수를 LangChain 도구로 등록 (모델이 호출 가능, 일반 함수 호출은 .invoke 필요)
def pdf_to_text(pdf_path: str) -> str:
    """PDF 파일의 경로를 입력받아 전체 텍스트를 추출하고 txt로 저장한 뒤 그 경로를 반환하는 함수

    Args:
        pdf_path (str): 변환할 PDF 파일의 경로
    """
    doc = pymupdf.open(pdf_path)  # PDF 파일 열기
    full_text = ""  # 전체 텍스트를 누적할 변수 (반드시 빈 문자열로 초기화)
    for page in doc:  # PDF의 각 페이지를 순회
        text = page.get_text()  # 현재 페이지의 텍스트 추출
        full_text += text + "\n------------------------\n"  # 페이지 구분선과 함께 누적

    pdf_file_name = os.path.basename(pdf_path)  # 경로에서 파일명만 분리 (예: Language_Models.pdf)
    pdf_file_name = os.path.splitext(pdf_file_name)[0]  # 확장자 제거 (예: Language_Models)
    txt_file_path = os.path.join(os.path.dirname(pdf_path), f"{pdf_file_name}.txt")  # 같은 폴더에 .txt 경로 생성
    with open(txt_file_path, "w", encoding="utf-8") as f:  # txt 파일을 쓰기 모드로 열기
        f.write(full_text)  # 추출한 전체 텍스트 저장

    return txt_file_path  # 저장한 txt 파일 경로 반환


@tool  # txt에서 저자를 찾는 도구로 등록
def find_author_txt(txt_file_path: str) -> str:
    """TXT 파일의 경로를 입력받아 문서의 저자가 누구인지 찾아 반환하는 함수

    Args:
        txt_file_path (str): 저자를 찾을 txt 파일의 경로
    """
    with open(txt_file_path, "r", encoding="utf-8") as f:  # txt 파일을 읽기 모드로 열기
        txt = f.read()  # 파일 전체 내용을 문자열로 읽기

    system_prompt = f"""
    너는 다음 글에서 저자(author)를 찾아내는 봇이다. 아래 글을 읽고,

    작성해야 하는 포맷은 다음과 같음
    # 문서 제목

    ## 저자
    - 이름(들)을 나열. 찾을 수 없으면 "저자 정보를 찾을 수 없음"이라고 적을 것

    ## 근거
    - 본문에서 저자라고 판단한 부분을 간단히 인용


    ============= 이하 텍스트 ================
    {txt[:10000]}
    """  # 저자 추출 형식을 지정하고 본문(최대 1만 자)을 끼워 넣는 시스템 프롬프트

    response = llm.invoke([  # ChatOpenAI에 메시지 리스트를 보내 응답 생성 (기존 client.chat.completions 대체)
        SystemMessage(content=system_prompt),  # 역할/지시문을 담은 시스템 메시지
    ])

    return response.content  # AIMessage 객체에서 실제 텍스트만 꺼내 반환


@tool  # PDF → 텍스트 변환 → 저자 찾기 → 저장까지 한 번에 수행하는 상위 도구
def find_author_pdf(pdf_path: str) -> str:
    """PDF 파일의 경로를 입력받아 텍스트로 변환 후 저자를 찾고, 결과를 txt로 저장한 뒤 반환하는 함수

    Args:
        pdf_path (str): 저자를 찾을 PDF 파일의 경로
    """
    txt_file_path = pdf_to_text.invoke(pdf_path)  # @tool 함수는 직접 호출 대신 .invoke로 실행 (PDF→txt)
    author = find_author_txt.invoke(txt_file_path)  # txt에서 저자 찾기 (.invoke로 호출)
    author_file_name = os.path.splitext(os.path.basename(pdf_path))[0] + "_author.txt"  # 결과 파일명 생성
    author_file_path = os.path.join(os.path.dirname(pdf_path), author_file_name)  # 원본과 같은 폴더에 저장 경로 생성
    with open(author_file_path, "w", encoding="utf-8") as f:  # 결과 파일 쓰기 모드로 열기
        f.write(author)  # 저자 찾기 결과 저장
    return author  # 저자 정보 반환


tools = [find_author_pdf, pdf_to_text, find_author_txt]  # 모델에 바인딩할 도구 목록 (모두 @tool 함수)
tool_dict = {  # 도구 이름 → 도구 객체 매핑 (tool_call 결과로 함수를 찾을 때 사용)
    "find_author_pdf": find_author_pdf,
    "pdf_to_text": pdf_to_text,
    "find_author_txt": find_author_txt,
}

llm_with_tools = llm.bind_tools(tools)  # LLM에 도구를 연결해 tool calling이 가능한 모델 생성


if __name__ == "__main__":  # 이 파일을 직접 실행할 때만 아래 데모 동작
    pdf_path = os.path.join(APP_DIR.parent, "4일차", "samples", "Language_Models.pdf")  # 저자를 찾을 PDF 경로

    messages = [  # tool calling 데모용 대화 메시지 구성 (5.2 노트북 시작 형태)
        HumanMessage(f"이 {pdf_path} 문서의 저자는 누구야?"),  # 사용자 요청 (질문 안에 경로를 넣어 모델이 도구 인자로 사용)
    ]

    response = llm_with_tools.invoke(messages)  # 모델이 어떤 도구를 쓸지 판단 (tool_calls 생성)
    messages.append(response)  # 모델 응답(AIMessage, tool_calls 포함)을 대화 기록에 추가

    for tool_call in response.tool_calls:  # 모델이 요청한 각 도구 호출을 순회
        selected_tool = tool_dict[tool_call["name"]]  # 이름으로 실제 도구 객체 선택
        tool_msg = selected_tool.invoke(tool_call)  # 도구 실행 → ToolMessage 반환
        messages.append(tool_msg)  # 도구 실행 결과를 대화 기록에 추가

    final = llm_with_tools.invoke(messages)  # 도구 결과를 바탕으로 모델이 최종 자연어 답변 생성
    print(final.content)  # 최종 답변 출력
    print("저자 정보를 성공적으로 찾았습니다.")  # 완료 메시지
