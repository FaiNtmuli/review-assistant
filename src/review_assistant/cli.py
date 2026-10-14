"""命令行入口。

几个子命令对应里程碑：doctor 验连通性，ingest 只看读入和解析，graph 看图长什么样，
run 跑完整流程。
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Annotated, Any, Optional

import typer
from langgraph.types import Command
from rich.console import Console
from rich.markdown import Markdown
from rich.rule import Rule

from .config import load_settings
from .diff_parser import parse_unified_diff, summarize
from .graph import build_graph, make_checkpointer
from .llm import build_chat_model, ping, structured_call
from .schemas import CommentOut, Report

app = typer.Typer(add_completion=False, no_args_is_help=True, help="基于 LangGraph 的代码审查助手")
console = Console()

ModelOpt = Annotated[Optional[str], typer.Option("--model", help="模型名")]
BaseUrlOpt = Annotated[Optional[str], typer.Option("--base-url", help="OpenAI 兼容接口地址")]


@app.command()
def doctor(model: ModelOpt = None, base_url: BaseUrlOpt = None) -> None:
    """连通性自检：确认地址、密钥、模型名，以及结构化输出能不能用。"""
    settings = load_settings(model=model, base_url=base_url)
    console.print(f"接口地址：{settings.base_url}")
    console.print(f"模型名：{settings.model}")
    console.print(f"密钥：{'已配置' if settings.api_key != 'not-needed' else '未配置（按本地服务处理）'}")

    try:
        console.print(f"文本调用：{ping(settings)}")
    except Exception as exc:  # noqa: BLE001 - 自检要把失败原样报出来
        console.print(f"[red]文本调用失败：{exc}[/red]")
        raise typer.Exit(code=1) from exc

    sample = "    1 + def div(a, b):\n    2 +     return a / b"
    try:
        result = structured_call(
            build_chat_model(settings),
            CommentOut,
            "你是代码审查者，按要求返回结构化结果，evidence 逐字引用代码原文。",
            f"文件 a.py 的改动如下：\n{sample}\n\n"
            "请给出一条意见：file_path 填 a.py，line_start 和 line_end 都填 2，"
            "evidence 填 return a / b。",
        )
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]结构化输出失败：{exc}[/red]")
        raise typer.Exit(code=1) from exc

    console.print(f"结构化输出：返回 {len(result.comments)} 条意见")
    if result.comments:
        first = result.comments[0]
        console.print(
            f"  示例：{first.file_path}:{first.line_start}-{first.line_end} "
            f"[{first.severity}] {first.title}"
        )
        console.print(f"  引用原文：{first.evidence!r}")
    console.print("[green]自检通过[/green]")


@app.command()
def checkpoint(
    kind: Annotated[str, typer.Option("--kind", help="memory 或 sqlite")] = "memory",
    db: Annotated[str, typer.Option("--db", help="sqlite 检查点文件")] = "checkpoints.db",
) -> None:
    """检查点自检：确认这一种检查点能读能写。"""
    saver = make_checkpointer(kind, db)
    console.print(f"检查点类型：{kind} → {type(saver).__name__}")
    if kind == "sqlite":
        console.print(f"文件：{db}")
    console.print("[green]检查点可用[/green]")


@app.command()
def graph() -> None:
    """打印图的结构，用来确认节点和连线符合设计。"""
    compiled = build_graph()
    console.print(compiled.get_graph().draw_mermaid())


def _resolve_source(
    from_git: str | None, patch: str | None, diff_text: str | None
) -> dict[str, str]:
    given = [value for value in (from_git, patch, diff_text) if value]
    if len(given) != 1:
        raise typer.BadParameter("改动来源三选一：--from-git 或 --patch 或 --diff-text")
    if from_git:
        return {"type": "git", "range": from_git}
    if patch:
        return {"type": "patch", "path": patch}
    return {"type": "text", "text": diff_text or ""}


def parse_paths(raw: str | None) -> list[str]:
    """把 --paths "app/,config/" 拆成关键词列表。"""
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


@app.command()
def ingest(
    from_git: Annotated[Optional[str], typer.Option("--from-git", help="git 提交范围，如 HEAD~1")] = None,
    patch: Annotated[Optional[str], typer.Option("--patch", help="patch 文件路径")] = None,
    diff_text: Annotated[Optional[str], typer.Option("--diff-text", help="直接给一段 diff 文本")] = None,
    numbered: Annotated[bool, typer.Option("--numbered", help="打印带行号的改动")] = False,
) -> None:
    """只跑读入和解析，不调模型。"""
    source = _resolve_source(from_git, patch, diff_text)

    from .nodes.ingest import ingest as ingest_node

    state = ingest_node({"diff_source": source})  # type: ignore[arg-type]
    changes = parse_unified_diff(state["diff_text"])
    console.print(f"来源：{state['source']}")
    console.print(f"解析结果：{summarize(changes)}")
    for change in changes:
        console.print(
            f"  {change.path}｜{change.change_type}｜新增 {len(change.added_lines)} 行"
            f"｜新增行号 {sorted(change.added_lines)[:10]}"
        )
    if numbered:
        for change in changes:
            console.print(Rule(change.path))
            console.print(change.render_numbered())


def _first_interrupt(snapshot: Any) -> Any:
    for task in getattr(snapshot, "tasks", ()) or ():
        for item in getattr(task, "interrupts", ()) or ():
            return getattr(item, "value", None)
    return None


SEVERITY_ORDER = {"high": 3, "medium": 2, "low": 1}


@app.command()
def show(
    report: Annotated[str, typer.Option("--report", help="已保存的 report.json")] = "out/report.json",
    min_severity: Annotated[str, typer.Option("--min-severity", help="只看这个级别以上：high / medium / low")] = "low",
    only_file: Annotated[Optional[str], typer.Option("--file", help="只看这个文件")] = None,
    unverified_only: Annotated[bool, typer.Option("--unverified-only", help="只看没通过校验的意见")] = False,
) -> None:
    """从已经落盘的报告里筛出要看的意见，不重新调模型。"""
    threshold = SEVERITY_ORDER.get(min_severity)
    if threshold is None:
        console.print(f"[red]不认识的级别：{min_severity}[/red]")
        raise typer.Exit(code=1)

    path = Path(report)
    if not path.is_file():
        console.print(f"[red]找不到报告文件：{path}[/red]")
        raise typer.Exit(code=1)
    parsed = Report.model_validate_json(path.read_text(encoding="utf-8"))

    shown = 0
    for file_report in parsed.files:
        if only_file and file_report.path != only_file:
            continue
        picked = [
            comment
            for comment in file_report.comments
            if SEVERITY_ORDER.get(comment.severity, 0) >= threshold
            and (comment.verified or not unverified_only)
        ]
        if not picked:
            continue
        console.print(f"[bold]{file_report.path}[/bold]（{file_report.kind}，意见 {len(picked)} 条）")
        for comment in picked:
            mark = "" if comment.verified else "[yellow]（未通过校验）[/yellow]"
            console.print(
                f"  {comment.line_start}-{comment.line_end} [{comment.severity}]"
                f" {comment.title}{mark}"
            )
        shown += len(picked)

    console.print(f"共筛出 {shown} 条，报告共 {parsed.stats.comments_total} 条")


def _print_report(report_payload: dict[str, Any] | None) -> None:
    if not report_payload:
        console.print("[yellow]没有拿到报告内容[/yellow]")
        return
    report = Report.model_validate(report_payload)
    console.print(Rule("审查报告"))
    console.print(Markdown(report.to_markdown()))
    console.print(Rule())


def _interactive_decision() -> dict[str, str]:
    console.print("  [bold]a[/bold] 通过并输出    [bold]r[/bold] 打回重做    [bold]x[/bold] 放弃")
    raw = typer.prompt("选择", default="a").strip().lower()
    if raw.startswith("a"):
        return {"decision": "approve"}
    if raw.startswith("r"):
        feedback = typer.prompt("打回意见").strip()
        return {"decision": "revise", "feedback": feedback}
    return {"decision": "abort"}


def _drive(
    compiled: Any,
    payload: Any,
    config: dict[str, Any],
    auto_approve: bool,
    reject_first: str | None,
    pause_at_review: bool = False,
) -> dict[str, Any]:
    """跑到人工确认就停下问一次，确认后接着跑，直到图自然结束。"""
    rejected = False
    while True:
        for chunk in compiled.stream(payload, config, stream_mode="updates"):
            for node, update in chunk.items():
                if node == "__interrupt__" or not isinstance(update, dict):
                    continue
                for line in update.get("history") or []:
                    console.print(f"[dim]  · {line}[/dim]")

        snapshot = compiled.get_state(config)
        interrupt_value = _first_interrupt(snapshot)
        if interrupt_value is None:
            return snapshot.values

        report_payload = (interrupt_value or {}).get("report") or snapshot.values.get("report")
        _print_report(report_payload)

        if pause_at_review:
            console.print("[cyan]已停在人工确认，进程直接退出，状态留在检查点里[/cyan]")
            return snapshot.values

        if reject_first and not rejected:
            rejected = True
            decision: dict[str, str] = {"decision": "revise", "feedback": reject_first}
            console.print(f"[yellow]按 --reject-first 打回重做：{reject_first}[/yellow]")
        elif auto_approve:
            decision = {"decision": "approve"}
            console.print("[cyan]按 --auto-approve 通过[/cyan]")
        else:
            decision = _interactive_decision()

        payload = Command(resume=decision)


@app.command()
def run(
    from_git: Annotated[Optional[str], typer.Option("--from-git", help="git 提交范围，如 HEAD~1")] = None,
    patch: Annotated[Optional[str], typer.Option("--patch", help="patch 文件路径")] = None,
    diff_text: Annotated[Optional[str], typer.Option("--diff-text", help="直接给一段 diff 文本")] = None,
    out: Annotated[str, typer.Option("--out", help="报告输出目录")] = "out",
    kind: Annotated[str, typer.Option("--checkpointer", help="memory 或 sqlite")] = "memory",
    db: Annotated[str, typer.Option("--db", help="sqlite 检查点文件")] = "checkpoints.db",
    thread: Annotated[Optional[str], typer.Option("--thread", help="会话 id，断点恢复时用它接上")] = None,
    paths: Annotated[
        Optional[str], typer.Option("--paths", help="只审查路径里包含这些关键词的文件，逗号分隔")
    ] = None,
    model: ModelOpt = None,
    base_url: BaseUrlOpt = None,
    max_concurrency: Annotated[int, typer.Option("--max-concurrency", help="同时在跑的模型调用数")] = 4,
    max_rounds: Annotated[int, typer.Option("--max-rounds", help="校验不通过时最多重做几轮")] = 3,
    auto_approve: Annotated[bool, typer.Option("--auto-approve", help="自动通过，不进入交互")] = False,
    pause_at_review: Annotated[
        bool, typer.Option("--pause-at-review", help="停在人工确认就退出，用来演示断点恢复")
    ] = False,
    reject_first: Annotated[
        Optional[str], typer.Option("--reject-first", help="第一次人工确认时打回并带上这段反馈")
    ] = None,
) -> None:
    """跑完整流程：读入 → 解析 → 分类 → 并发审查 → 校验 → 汇总 → 人工确认 → 输出。"""
    source = _resolve_source(from_git, patch, diff_text)
    path_filter = parse_paths(paths)
    settings = load_settings(
        model=model,
        base_url=base_url,
        max_concurrency=max_concurrency,
        max_rounds=max_rounds,
    )
    thread_id = thread or uuid.uuid4().hex[:12]
    config: dict[str, Any] = {
        "configurable": {"thread_id": thread_id},
        "max_concurrency": settings.max_concurrency,
    }

    console.print(
        f"会话 {thread_id}｜模型 {settings.model}｜检查点 {kind}｜并发 {settings.max_concurrency}"
    )
    if path_filter:
        console.print(f"只审查路径里包含 {path_filter} 的文件")
    compiled = build_graph(make_checkpointer(kind, db))

    initial: dict[str, Any] = {
        "diff_source": source,
        "output_dir": out,
        "path_filter": path_filter,
        "settings": settings.for_state(),
        "drafts": {},
        "verdicts": {},
        "revision_round": 0,
        "human_feedback": "",
        "history": [],
    }

    if pause_at_review:
        console.print(f"恢复命令：review resume --thread {thread_id} --db {db} --auto-approve")
    values = _drive(compiled, initial, config, auto_approve, reject_first, pause_at_review)
    decision = values.get("decision") or "abort"
    console.print(Rule("结果"))
    if decision == "approve":
        for path in values.get("output_paths", []):
            console.print(f"[green]已输出[/green] {path}")
    else:
        console.print("[yellow]已放弃，没有输出报告[/yellow]")


@app.command()
def resume(
    thread: Annotated[str, typer.Option("--thread", help="会话 id")],
    db: Annotated[str, typer.Option("--db", help="sqlite 检查点文件")] = "checkpoints.db",
    auto_approve: Annotated[bool, typer.Option("--auto-approve", help="自动通过，不进入交互")] = False,
) -> None:
    """进程退出后从断点接着跑。检查点必须是 sqlite，内存检查点不跨进程。"""
    settings = load_settings()
    config: dict[str, Any] = {
        "configurable": {"thread_id": thread},
        "max_concurrency": settings.max_concurrency,
    }
    compiled = build_graph(make_checkpointer("sqlite", db))

    snapshot = compiled.get_state(config)
    if not snapshot or not snapshot.values:
        console.print(f"[red]检查点 {db} 里没有会话 {thread}[/red]")
        raise typer.Exit(code=1)
    if _first_interrupt(snapshot) is None:
        console.print(f"[yellow]会话 {thread} 没有停在人工确认上[/yellow]")
        raise typer.Exit(code=1)

    console.print(f"会话 {thread}｜从 {db} 恢复")
    payload: Any = Command(resume={"decision": "approve"}) if auto_approve else None
    values = _drive(compiled, payload, config, auto_approve, None)
    decision = values.get("decision") or "abort"
    console.print(Rule("结果"))
    if decision == "approve":
        for path in values.get("output_paths", []):
            console.print(f"[green]已输出[/green] {path}")
    else:
        console.print("[yellow]已放弃，没有输出报告[/yellow]")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
