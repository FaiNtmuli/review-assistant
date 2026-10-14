"""人工确认。流程在这里原地暂停，状态存进 checkpointer，进程可以退出。"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END
from langgraph.types import interrupt

from ..state import ReviewState, log

CHOICES = ("approve", "revise", "abort")


def human_gate(state: ReviewState) -> dict[str, Any]:
    payload = {
        "question": "报告已生成，选择下一步",
        "choices": list(CHOICES),
        "report": state.get("report") or {},
    }
    answer = interrupt(payload)

    if isinstance(answer, dict):
        choice = str(answer.get("decision", "")).strip().lower()
        feedback = str(answer.get("feedback", "")).strip()
    else:
        choice = str(answer).strip().lower()
        feedback = ""

    if choice not in CHOICES:
        choice, feedback = "abort", f"收到无法识别的选择 {choice!r}，按放弃处理"

    return {
        "decision": choice,
        "human_feedback": feedback,
        "revision_round": 0,
        **log(f"human_gate：选择 {choice}" + (f"，反馈：{feedback}" if feedback else "")),
    }


def route_after_human_gate(state: ReviewState) -> str:
    decision = state.get("decision", "abort")
    if decision == "approve":
        return "finalize"
    if decision == "revise":
        return "revise"
    return END
