from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
API_ROOT = ROOT / "services" / "api"
sys.path.insert(0, str(API_ROOT))

from services.history_workflow_service import (  # noqa: E402
    _book_introduction_length,
    _compact_length,
    _repeated_sentence_ratio,
    build_history_step_messages,
    load_history_workflow_prompt,
)


def load_env(path: Path) -> None:
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def choose_project(db: dict, project_id: str | None) -> dict:
    projects = db.get("projects") or []
    if project_id:
        project = next((item for item in projects if item.get("id") == project_id), None)
        if project is None:
            raise RuntimeError(f"project not found: {project_id}")
        return project
    project = next((
        item for item in projects
        if item.get("raw_script") and (item.get("history_workflow") or {}).get("outputs", {}).get("1")
    ), None)
    if project is None:
        raise RuntimeError("no project has both raw_script and Step 1 output")
    return project


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay one exact Step 2 request against one endpoint.")
    parser.add_argument("--project-id")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--endpoint")
    parser.add_argument("--model")
    parser.add_argument("--api-key-env", default="OPENAI_API_KEY")
    parser.add_argument("--reasoning-effort", default="high", choices=("none", "low", "medium", "high"))
    args = parser.parse_args()

    load_env(ROOT / ".env.local")
    endpoint = (args.endpoint or os.getenv("OPENAI_ENDPOINT") or "https://api.openai.com/v1").rstrip("/")
    model = args.model or os.getenv("OPENAI_MODEL") or "gpt-5.6"
    api_key = os.getenv(args.api_key_env, "").strip()
    if not api_key:
        raise RuntimeError(f"{args.api_key_env} is not configured")

    db = json.loads((ROOT / "storage" / "db.json").read_text(encoding="utf-8"))
    project = choose_project(db, args.project_id)
    workflow = project.get("history_workflow") or {}
    outputs = workflow.get("outputs") or {}
    stage_messages = workflow.get("messages") or {}
    book_title = str(project.get("promotion_book_title") or "国之脊梁").strip().strip("《》")
    formatted_book_title = f"《{book_title}》"

    load_history_workflow_prompt.cache_clear()
    messages = build_history_step_messages(
        2,
        str(project.get("raw_script") or ""),
        outputs,
        stage_messages,
        book_title,
    )
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "max_completion_tokens": 12000,
        "reasoning_effort": args.reasoning_effort,
    }
    payload_bytes = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    prompt_hash = hashlib.sha256(payload_bytes).hexdigest()
    runs = []
    for index in range(args.runs):
        request = urllib.request.Request(
            f"{endpoint}/chat/completions",
            data=payload_bytes,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=300) as response:
                body = json.loads(response.read().decode("utf-8"))
                header_request_id = response.headers.get("x-request-id") or response.headers.get("request-id")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"endpoint returned HTTP {exc.code}: {detail[:800]}") from exc
        elapsed = round(time.perf_counter() - started, 3)
        choice = body["choices"][0]
        content = str(choice.get("message", {}).get("content") or "").strip()
        runs.append({
            "run": index + 1,
            "elapsed_seconds": elapsed,
            "request_id": body.get("id") or header_request_id or "",
            "response_model": body.get("model") or "",
            "finish_reason": choice.get("finish_reason") or "",
            "usage": body.get("usage") or {},
            "metrics": {
                "character_count": _compact_length(content),
                "repeated_sentence_ratio": _repeated_sentence_ratio(content),
                "book_introduction_length": _book_introduction_length(content, formatted_book_title),
            },
            "content": content,
        })
        print(json.dumps({key: value for key, value in runs[-1].items() if key != "content"}, ensure_ascii=False))

    report = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "project_id": project.get("id"),
        "project_name": project.get("name"),
        "endpoint": endpoint,
        "requested_model": model,
        "reasoning_effort": args.reasoning_effort,
        "prompt_sha256": prompt_hash,
        "message_count": len(messages),
        "message_characters": sum(len(item.get("content") or "") for item in messages),
        "messages": messages,
        "runs": runs,
    }
    diagnostics = ROOT / "storage" / "diagnostics"
    diagnostics.mkdir(parents=True, exist_ok=True)
    output_path = diagnostics / f"step2_endpoint_validation_{datetime.now():%Y%m%d_%H%M%S}.json"
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"report={output_path}")


if __name__ == "__main__":
    main()
