from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

# ── 경로 ──────────────────────────────────────────
BASE = Path(__file__).resolve().parent
load_dotenv(BASE.parent / ".env")
DOC_LIBRARY = BASE / "samples" / "pdf_samples"
CATALOG_DIR = DOC_LIBRARY / "_catalog"
DEFAULT_OUTPUT_DIR = DOC_LIBRARY / "_organized_by_topic"

client = OpenAI()


def slugify_topic(name: str) -> str:
    cleaned = re.sub(r"[^\w가-힣\- ]+", "", name).strip()
    cleaned = re.sub(r"\s+", "_", cleaned)
    return cleaned[:60] or "기타"


def list_pdf_files() -> str:
    """pdf_samples 안 PDF 목록 반환."""
    names = sorted(p.name for p in DOC_LIBRARY.glob("*.pdf"))
    return json.dumps({"count": len(names), "pdf_files": names}, ensure_ascii=False, indent=2)


def read_catalog_metadata() -> str:
    """_catalog/index.json과 summary를 읽어 문서 메타데이터 반환."""
    index_path = CATALOG_DIR / "index.json"
    if not index_path.exists():
        return json.dumps({"error": "index.json이 없습니다."}, ensure_ascii=False)

    data = json.loads(index_path.read_text(encoding="utf-8"))
    docs = []
    for doc in data.get("documents", []):
        summary_path = CATALOG_DIR / doc.get("summary_file", "")
        summary_text = summary_path.read_text(encoding="utf-8") if summary_path.exists() else ""

        one_line = ""
        lines = summary_text.splitlines()
        for i, line in enumerate(lines):
            if "한 줄 요약" in line and i + 1 < len(lines):
                one_line = lines[i + 1].strip()
                break

        docs.append(
            {
                "pdf_name": doc.get("pdf_name", ""),
                "category": doc.get("category", "기타"),
                "keywords": doc.get("keywords", []),
                "one_line_summary": one_line,
            }
        )

    return json.dumps({"documents": docs}, ensure_ascii=False, indent=2)


def _heuristic_topic(category: str, keywords: list[str]) -> str:
    c = (category or "").strip().lower()
    kw = " ".join(k.lower() for k in (keywords or []))

    if "규정" in c or "학칙" in kw:
        return "학교규정"
    if "보도자료" in c or "경영실적" in kw:
        return "기업보도자료"
    if "논문" in c:
        if any(token in kw for token in ["vision", "vit", "image", "contrastive"]):
            return "논문_비전AI"
        if any(token in kw for token in ["speech", "language", "multimodal", "data2vec"]):
            return "논문_멀티모달"
        if any(token in kw for token in ["agent", "llm", "autonomous"]):
            return "논문_에이전트"
        return "논문_기타"
    return "기타"


def propose_topic_groups(max_topics: int = 8, min_docs_per_topic: int = 1) -> str:
    """카탈로그 기반으로 주제별 그룹 제안."""
    raw = json.loads(read_catalog_metadata())
    if "error" in raw:
        return json.dumps(raw, ensure_ascii=False)

    groups: dict[str, list[str]] = {}
    for doc in raw.get("documents", []):
        topic = _heuristic_topic(doc.get("category", ""), doc.get("keywords", []))
        groups.setdefault(topic, []).append(doc.get("pdf_name", ""))

    # 조건 적용
    filtered = []
    for topic, files in groups.items():
        files = sorted([f for f in files if f])
        if len(files) >= max(1, min_docs_per_topic):
            filtered.append({"topic": topic, "pdf_files": files})

    filtered = sorted(filtered, key=lambda x: (-len(x["pdf_files"]), x["topic"]))[:max_topics]
    return json.dumps({"groups": filtered}, ensure_ascii=False, indent=2)


def apply_topic_organization(
    groups_json: str,
    mode: str = "copy",
    dry_run: bool = False,
    output_dir: str | None = None,
) -> str:
    """
    주제별 폴더 생성 후 PDF를 copy/move.
    mode: copy | move
    """
    try:
        payload = json.loads(groups_json)
    except json.JSONDecodeError as e:
        return json.dumps({"error": f"groups_json 파싱 실패: {e}"}, ensure_ascii=False)

    groups = payload.get("groups", [])
    if not isinstance(groups, list) or not groups:
        return json.dumps({"error": "groups가 비어있거나 형식이 올바르지 않습니다."}, ensure_ascii=False)

    out_root = Path(output_dir) if output_dir else DEFAULT_OUTPUT_DIR
    operations = []
    errors = []

    for item in groups:
        topic = item.get("topic", "기타")
        pdf_files = item.get("pdf_files", [])
        topic_dir = out_root / slugify_topic(topic)

        if not dry_run:
            topic_dir.mkdir(parents=True, exist_ok=True)

        for name in pdf_files:
            src = DOC_LIBRARY / name
            dst = topic_dir / name
            if not src.exists():
                errors.append(f"파일 없음: {name}")
                continue

            op = {"topic": topic, "action": mode, "src": str(src), "dst": str(dst)}
            operations.append(op)

            if dry_run:
                continue

            try:
                if mode == "move":
                    shutil.move(str(src), str(dst))
                else:
                    shutil.copy2(src, dst)
            except Exception as e:  # noqa: BLE001
                errors.append(f"{name}: {e}")

    manifest = {
        "output_dir": str(out_root),
        "mode": mode,
        "dry_run": dry_run,
        "total_operations": len(operations),
        "errors": errors,
        "operations": operations,
    }

    if not dry_run:
        out_root.mkdir(parents=True, exist_ok=True)
        log_path = out_root / "_organization_manifest.json"
        log_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        manifest["manifest_path"] = str(log_path)

    return json.dumps(manifest, ensure_ascii=False, indent=2)


AGENT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_pdf_files",
            "description": "pdf_samples 폴더의 PDF 파일 목록.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_catalog_metadata",
            "description": "_catalog/index.json과 summary를 읽어 분류용 메타데이터를 반환.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_topic_groups",
            "description": "문서들을 주제별 그룹으로 제안한다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "max_topics": {"type": "integer"},
                    "min_docs_per_topic": {"type": "integer"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "apply_topic_organization",
            "description": "주제별 폴더를 만들고 PDF를 copy/move로 정리한다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "groups_json": {"type": "string"},
                    "mode": {"type": "string", "enum": ["copy", "move"]},
                    "dry_run": {"type": "boolean"},
                    "output_dir": {"type": "string"},
                },
                "required": ["groups_json"],
            },
        },
    },
]

TOOL_FUNCTIONS = {
    "list_pdf_files": lambda **_: list_pdf_files(),
    "read_catalog_metadata": lambda **_: read_catalog_metadata(),
    "propose_topic_groups": propose_topic_groups,
    "apply_topic_organization": apply_topic_organization,
}

AGENT_SYSTEM = """
너는 pdf_samples 문서 정리 에이전트다.

반드시 아래 순서로 도구를 호출한다:
1) list_pdf_files
2) read_catalog_metadata
3) propose_topic_groups
4) apply_topic_organization

규칙:
- 사용자가 따로 말하지 않으면 mode='copy'로 정리한다.
- output_dir는 기본값을 사용한다.
- 최종 답변에는 생성된 폴더 경로와 처리 개수, 오류 유무를 요약한다.
- 답변은 한국어로 작성한다.
""".strip()


def run_agent(command: str, max_rounds: int = 8) -> str:
    messages = [
        {"role": "system", "content": AGENT_SYSTEM},
        {"role": "user", "content": command},
    ]

    for _ in range(max_rounds):
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.1,
            messages=messages,
            tools=AGENT_TOOLS,
        )
        msg = resp.choices[0].message
        if not msg.tool_calls:
            return msg.content or ""

        messages.append(msg)
        for tc in msg.tool_calls:
            fn = tc.function.name
            args = json.loads(tc.function.arguments or "{}")
            result = TOOL_FUNCTIONS[fn](**args)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                }
            )

    return "tool 호출 최대 횟수 초과"


if __name__ == "__main__":
    user_command = (
        "pdf_samples에 있는 PDF를 주제별로 폴더 생성해서 정리해줘. "
        "기본 설정으로 진행해."
    )
    print(run_agent(user_command))
