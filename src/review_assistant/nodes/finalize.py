"""通过后把报告落盘。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..schemas import Report
from ..state import ReviewState, log


def finalize(state: ReviewState) -> dict[str, Any]:
    report = Report.model_validate(state["report"] or {})
    output_dir = Path(state.get("output_dir") or "out")
    output_dir.mkdir(parents=True, exist_ok=True)

    json_path = output_dir / "report.json"
    markdown_path = output_dir / "report.md"
    json_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    markdown_path.write_text(report.to_markdown(), encoding="utf-8")

    return {
        "output_paths": [str(json_path), str(markdown_path)],
        **log(f"finalize：写入 {json_path} 和 {markdown_path}"),
    }
