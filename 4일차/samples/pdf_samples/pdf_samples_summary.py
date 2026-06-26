import json
import os
from pathlib import Path

import pymupdf
from dotenv import load_dotenv
from openai import OpenAI

# 이 파일 기준 경로: .../4일차/samples/pdf_samples/pdf_samples_summary.py
BASE_DIR = Path(__file__).resolve().parent  # pdf_samples/
CATALOG_DIR = BASE_DIR / "_catalog"

# 4.5 노트북과 동일하게: .env는 프로젝트 루트(실습/)에 있음
# pdf_samples -> samples -> 4일차 -> 실습
ROOT_DIR = BASE_DIR.parents[3]
load_dotenv(ROOT_DIR / ".env")


def _get_client() -> OpenAI | None:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None
    return OpenAI(api_key=api_key)


# step1: pdf_samples 폴더 내 PDF 목록 반환 tool (4.5 노트북 naming과 맞춤)
def list_documents() -> str:
    """
    pdf_samples 폴더 내 PDF 파일명을 JSON 문자열로 반환
    예: {"count": 2, "pdf_files": ["a.pdf", "b.pdf"]}
    """
    pdf_files = sorted([p.name for p in BASE_DIR.glob("*.pdf")])
    return json.dumps({"count": len(pdf_files), "pdf_files": pdf_files}, ensure_ascii=False)


def _read_pdf_text(pdf_path: Path) -> str:
    doc = pymupdf.open(pdf_path)
    return "\n".join(page.get_text() for page in doc)


# step2: pdf 파일 요약 tool
def summarize_pdf(pdf_name: str, max_chars: int = 12000) -> str:
    """
    pdf_name(파일명)을 받아 요약 결과를 JSON 문자열로 반환
    예: {"pdf_name": "...", "summary": "..."}
    """
    pdf_path = BASE_DIR / pdf_name
    if not pdf_path.exists():
        return json.dumps({"error": f"파일이 없습니다: {pdf_name}"}, ensure_ascii=False)

    client = _get_client()
    if client is None:
        return json.dumps(
            {"error": f"{ROOT_DIR / '.env'}에 OPENAI_API_KEY가 없습니다.", "pdf_name": pdf_name},
            ensure_ascii=False,
        )

    text = _read_pdf_text(pdf_path)
    snippet = text[:max_chars]

    system_prompt = f"""
너는 다음 PDF에서 추출된 텍스트를 요약하는 봇이다.
아래 텍스트를 읽고 다음 포맷으로 한국어로 작성하라.

# 제목

## 핵심 요약 (7줄 이내)

## 주요 포인트 (bullet 5개)

## 키워드 (쉼표로 5~10개)

============ 이하 텍스트 ================
{snippet}
""".strip()

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.2,
            messages=[{"role": "system", "content": system_prompt}],
        )
        summary = response.choices[0].message.content
        return json.dumps({"pdf_name": pdf_name, "summary": summary}, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"error": str(exc), "pdf_name": pdf_name}, ensure_ascii=False)


# step3: 요약본을 txt로 저장하는 tool
def save_summary_txt(pdf_name: str, summary: str) -> str:
    """
    summary 텍스트를 _catalog/<stem>.summary.txt 로 저장하고 경로를 JSON으로 반환
    예: {"saved_to": "..."}
    """
    CATALOG_DIR.mkdir(parents=True, exist_ok=True)
    out_path = CATALOG_DIR / f"{Path(pdf_name).stem}.summary.txt"
    out_path.write_text(summary, encoding="utf-8")
    return json.dumps({"saved_to": str(out_path)}, ensure_ascii=False)


# 4.5 노트북 스타일: tool schema + name → function 매핑
AGENT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_documents",
            "description": "pdf_samples 폴더의 PDF 파일명 목록.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "summarize_pdf",
            "description": "지정한 PDF 파일을 요약해 JSON으로 반환.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pdf_name": {"type": "string"},
                    "max_chars": {"type": "integer"},
                },
                "required": ["pdf_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_summary_txt",
            "description": "요약 텍스트를 _catalog/<stem>.summary.txt 로 저장.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pdf_name": {"type": "string"},
                    "summary": {"type": "string"},
                },
                "required": ["pdf_name", "summary"],
            },
        },
    },
]

TOOL_FUNCTIONS = {
    "list_documents": lambda **_: list_documents(),
    "summarize_pdf": summarize_pdf,
    "save_summary_txt": save_summary_txt,
}


# (선택) 전체 PDF를 돌며 요약 파일 생성하는 실행 예시
def summarize_all_pdfs() -> None:
    pdf_list = json.loads(list_documents())["pdf_files"]
    for pdf_name in pdf_list:
        result = json.loads(summarize_pdf(pdf_name))
        if "error" in result:
            print("[FAIL]", pdf_name, "->", result["error"])
            continue
        saved = json.loads(save_summary_txt(pdf_name, result["summary"]))
        print("[OK]", pdf_name, "->", saved["saved_to"])


if __name__ == "__main__":
    summarize_all_pdfs()
