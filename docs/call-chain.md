# 调用链

骨架是函数调用，同时标注控制流、数据依赖、状态写入和副作用。

标签约定：

- `⟶ result` 表示把返回值绑定到 result。
- `R:` 读取函数边界之外已有的数据；`W:` 修改函数边界之外已有的状态。
- 副作用：`[网]` 网络调用，`[印]` 输出，`[进]` 进程/命令，`[文]` 文件读写，`[存]` 持久化。
- 框架内部（typer、rich、pydantic、langgraph 的调度实现）不展开，只在调用处保留摘要。

## 〇 总览

```
main()
    app()                                                   # typer 按子命令分发  [印]
        run(...)  → 见第一节
        resume(...)  → 见第十一节
        doctor(...) / ingest(...) / graph() / show(...) / checkpoint(...)  → 见第十一节

run(from_git, patch, diff_text, out, kind, db, thread, paths,
    model, base_url, max_concurrency, max_rounds,
    auto_approve, pause_at_review, reject_first)                                 [网][印][进][文][存]
    _drive(compiled, initial, config, ...) ⟶ values
        stream(payload, config, stream_mode="updates") ⟶ 生成器                   [网][文][存]
            # 下面九个节点由 LangGraph 依连线调度。每个节点收到共享状态，
            # 返回值按合并规则写回状态；节点之间不直接互调。
            ingest(state) ⟶ {diff_text, source, history}
            parse_diff(state) ⟶ {files, history}
            classify(state) ⟶ {files, history}
            fan_out_files(state) ⟶ [Send("review_file", {file, settings}), ...] 或 "aggregate"
                review_file({file, settings}) ⟶ {drafts, history}     # 每文件一路，并发
            verify(state) ⟶ {verdicts, history}
            route_after_verify(state) ⟶ "revise" | "aggregate"
                revise(state) ⟶ {drafts, revision_round, human_feedback, history}
                verify(state)                                          # 回到校验，最多 max_rounds 轮
            aggregate(state) ⟶ {report, history}
            human_gate(state) ⟶ {decision, human_feedback, revision_round, history}
            route_after_human_gate(state) ⟶ "finalize" | "revise" | END
                finalize(state) ⟶ {output_paths, history}
        get_state(config) ⟶ snapshot
        _first_interrupt(snapshot) ⟶ 中断载荷 | None
        _print_report(...) / _interactive_decision() ⟶ decision
        Command(resume=decision)                               # 带着人的选择重新进入 human_gate
```

## 一 入口

```
run(from_git, patch, diff_text, out, kind, db, thread, paths,
    model, base_url, max_concurrency, max_rounds,
    auto_approve, pause_at_review, reject_first)
    source = _resolve_source(from_git, patch, diff_text) ⟶ dict
        given = [value for value in (from_git, patch, diff_text) if value]
        if len(given) != 1:
            raise BadParameter("改动来源三选一：--from-git 或 --patch 或 --diff-text")
        if from_git:
            return {"type": "git", "range": from_git}
        if patch:
            return {"type": "patch", "path": patch}
        return {"type": "text", "text": diff_text or ""}

    path_filter = parse_paths(paths) ⟶ list[str]
        if not raw:
            return []
        return [part.strip() for part in raw.split(",") if part.strip()]

    settings = load_settings(model=, base_url=, max_concurrency=, max_rounds=) ⟶ Settings
    # 见第二节

    thread_id = thread or uuid4().hex[:12]
    # 未给 --thread 时随机生成会话号，供检查点区分不同运行

    config = {"configurable": {"thread_id": thread_id},
              "max_concurrency": settings.max_concurrency}

    print(f"会话 {thread_id}｜模型 {settings.model}｜检查点 {kind}｜并发 {settings.max_concurrency}")   [印]
    if path_filter:
        print(f"只审查路径里包含 {path_filter} 的文件")                                        [印]

    compiled = build_graph(make_checkpointer(kind, db)) ⟶ CompiledStateGraph
    # 见第三节

    initial = {
        "diff_source": source,          # 改动从哪来
        "output_dir": out,              # 报告写到哪
        "path_filter": path_filter,     # 只审哪些文件
        "settings": settings.for_state(),   # 进图的那份不带密钥
        "drafts": {}, "verdicts": {},
        "revision_round": 0, "human_feedback": "", "history": [],
    }
    # W: 这是整张图的初始状态，后续所有节点都在它之上累积

    if pause_at_review:
        print(f"恢复命令：review resume --thread {thread_id} --db {db} --auto-approve")        [印]

    values = _drive(compiled, initial, config, auto_approve, reject_first, pause_at_review) ⟶ dict
    decision = values.get("decision") or "abort"
    print(Rule("结果"))                                                                       [印]
    if decision == "approve":
        for path in values.get("output_paths", []):
            print(f"已输出 {path}")                                                            [印]
    else:
        print("已放弃，没有输出报告")                                                           [印]
    return None
```

## 二 配置

```
load_settings(env_file=None, **overrides) ⟶ Settings
    load_dotenv(env_file, override=False)                      # 读 .env 注入环境变量   [文]
    R: 环境变量、.env 文件
    resolved = {
        "model": getenv("REVIEW_MODEL", DEFAULT_MODEL),
        "base_url": getenv("REVIEW_BASE_URL", DEFAULT_BASE_URL),
        "api_key": resolve_api_key(),
        "temperature": float(getenv("REVIEW_TEMPERATURE", "0")),
        "max_concurrency": int(getenv("REVIEW_MAX_CONCURRENCY", "4")),
        "max_rounds": int(getenv("REVIEW_MAX_ROUNDS", "3")),
    }
    for key, value in overrides.items():
        if value is not None:
            resolved[key] = value                              # 命令行参数覆盖环境变量
    return Settings(**resolved)

resolve_api_key() ⟶ str
    R: 环境变量
    return getenv("REVIEW_API_KEY") or getenv("DEEPSEEK_API_KEY") \
           or getenv("OPENAI_API_KEY") or "not-needed"

Settings.for_state() ⟶ dict
    data = model_dump()
    data.pop("api_key", None)                                  # 密钥不进状态，检查点会落盘
    return data

Settings.from_state(data) ⟶ Settings                            # 每个要模型的节点都先调它
    settings = cls(**(data or {}))
    if not settings.api_key:
        settings.api_key = resolve_api_key()                   # 从环境变量补回密钥
    return settings

Settings.__del__ 无；Settings 是 pydantic 模型，无副作用
```

## 三 图的组装

```
build_graph(checkpointer=None) ⟶ CompiledStateGraph
    builder = StateGraph(ReviewState)
    builder.add_node("ingest", ingest) ... add_node("finalize", finalize)   # 注册九个节点，共九行
    builder.add_edge(START, "ingest")  →  ingest → parse_diff → classify    # 三段普通连线
    builder.add_conditional_edges("classify", fan_out_files, ["review_file", "aggregate"])
    builder.add_edge("review_file", "verify")                  # 全部并发分支跑完才汇合
    builder.add_conditional_edges("verify", route_after_verify, ["revise", "aggregate"])
    builder.add_edge("revise", "verify")                       # 重试环
    builder.add_edge("aggregate", "human_gate")
    builder.add_conditional_edges("human_gate", route_after_human_gate,
                                  ["finalize", "revise", END])
    builder.add_edge("finalize", END)
    return compile(checkpointer=checkpointer or make_checkpointer("memory")) ⟶ CompiledStateGraph

make_checkpointer(kind="memory", path="checkpoints.db") ⟶ BaseCheckpointSaver
    if kind == "memory":
        return InMemorySaver(serde=make_serde())
    if kind == "sqlite":
        connection = connect(path, check_same_thread=False) ⟶ sqlite3.Connection      [存]
        return SqliteSaver(connection, serde=make_serde())
    raise ValueError(f"不认识的检查点类型：{kind}")

make_serde() ⟶ JsonPlusSerializer
    # 状态里的自定义类型要在序列化器里显式放行，否则读检查点会告警
    return JsonPlusSerializer(allowed_msgpack_modules=[...])

log(message) ⟶ {"history": [message]}
    # 节点返回它来追加一条过程记录；history 的合并规则是追加
```

## 四 驱动循环

```
_drive(compiled, payload, config, auto_approve, reject_first, pause_at_review=False) ⟶ dict  [网][印][进][文][存]
    rejected = False
    while True:
        # payload 首轮是初始状态；之后每次是 Command(resume=decision)
        for chunk in stream(payload, config, stream_mode="updates") ⟶ 生成器                [网][文][存]
            for node, update in chunk.items():
                if node == "__interrupt__" or not isinstance(update, dict):
                    continue
                for line in update.get("history") or []:
                    print(f"  · {line}")                                                    [印]
            # 每次 stream 返回一个节点的更新；history 在此被打印

        snapshot = get_state(config) ⟶ StateSnapshot
        interrupt_value = _first_interrupt(snapshot) ⟶ 中断载荷 | None
            for task in getattr(snapshot, "tasks", ()) or ():
                for item in getattr(task, "interrupts", ()) or ():
                    return getattr(item, "value", None)
            return None

        if interrupt_value is None:
            return snapshot.values                    # 图跑完了

        report_payload = (interrupt_value or {}).get("report") or snapshot.values.get("report")
        _print_report(report_payload)
            if not report_payload:
                print("没有拿到报告内容")                                                   [印]
                return None
            report = Report.model_validate(report_payload)
            print(Rule("审查报告"))                                                          [印]
            print(Markdown(report.to_markdown()))                                            [印]
            print(Rule())                                                                    [印]

        if pause_at_review:
            print("已停在人工确认，进程直接退出，状态留在检查点里")                              [印]
            return snapshot.values              # 演示断点恢复：不恢复图，直接返回

        if reject_first and not rejected:
            rejected = True
            decision = {"decision": "revise", "feedback": reject_first}
            print(f"按 --reject-first 打回重做：{reject_first}")                             [印]
        elif auto_approve:
            decision = {"decision": "approve"}
            print("按 --auto-approve 通过")                                                  [印]
        else:
            decision = _interactive_decision() ⟶ dict
                print("  a 通过并输出    r 打回重做    x 放弃")                               [印]
                raw = prompt("选择", default="a").strip().lower()
                if raw.startswith("a"):
                    return {"decision": "approve"}
                if raw.startswith("r"):
                    feedback = prompt("打回意见").strip()
                    return {"decision": "revise", "feedback": feedback}
                return {"decision": "abort"}

        payload = Command(resume=decision)          # 回到被中断的 human_gate
        # 循环继续；打回时图会再次跑到 human_gate，于是这里再问一次
```

## 五 读入

```
ingest(state) ⟶ {"diff_text", "source", "history"}                                     [进][文]
    source = state.get("diff_source") or {}
    kind = source.get("type", "git")

    if kind == "git":
        rev_range = source.get("range") or "HEAD~1"
        text = git_diff(rev_range) ⟶ str
            command = ["git", "diff", "--no-color", "--unified=3", rev_range]
            proc = run(command, capture_output=True, text=True, encoding="utf-8",
                       errors="replace") ⟶ CompletedProcess          # subprocess.run   [进][文]
            R: 仓库工作区与提交内容
            if proc.returncode != 0:
                raise RuntimeError(f"git diff 失败：{' '.join(command)}\n{proc.stderr.strip()}")
            return proc.stdout
        label = f"git {rev_range}"
    elif kind == "patch":
        path = Path(source.get("path", ""))
        if not path.is_file():
            raise FileNotFoundError(f"找不到改动文件：{path}")
        text = read_text(path, encoding="utf-8", errors="replace") ⟶ str                      [文]
        label = f"patch {path}"
    elif kind == "text":
        text = source.get("text", "")
        label = "内联改动"
    else:
        raise ValueError(f"不认识的改动来源：{kind}")

    if not text.strip():
        raise ValueError(f"{label} 里没有任何改动")

    return {"diff_text": text, "source": label,
            **log(f"ingest：读入 {label}，{len(text.splitlines())} 行 diff")}
    # W: diff_text、source、history 写回共享状态
```

## 六 解析与扇出

```
parse_diff(state) ⟶ {"files", "history"}
    changes = parse_unified_diff(state.get("diff_text", "")) ⟶ list[FileChange]
        # 不展开：见下方摘要
        # 逐行扫描 diff 文本，遇到 "diff --git" 开新文件、遇到 "@@" 开新 hunk，
        # 其余行按首字符 +、-、空格 归入新增行、删除行或上下文行。
        # 新增行记录为 {新文件行号: 行内容}，这是后续校验行号的唯一依据。
        # 用内部闭包 flush() 在文件切换时收尾；flush 的 W: files、current
        # 无副作用；返回 FileChange 列表
    mapping = {change.path: change for change in changes if change.path}
    added = sum(len(change.added_lines) for change in mapping.values())
    return {"files": mapping, **log(f"parse_diff：{len(mapping)} 个文件，{added} 行新增")}
    # W: files、history

fan_out_files(state) ⟶ list[Send] | "aggregate"                    # 条件连线的判定函数
    files = state.get("files") or {}
    if not files:
        return "aggregate"
    payload_settings = state.get("settings") or {}
    return [
        Send("review_file", {"file": change.model_dump(), "settings": payload_settings})
        for change in files.values()
        if not change.is_binary
    ] or "aggregate"
    # 每一路只带一个文件和配置；被分派的节点拿不到共享状态里的其他字段

summarize(changes) ⟶ str                                           # 叶：命令行摘要
    return f"{len(files)} 个文件，{total_added} 行新增"
```

## 七 分类

```
classify(state) ⟶ {"files", "history"}
    files = state.get("files") or {}
    patterns = state.get("path_filter") or []
    selected, dropped = filter_paths(files, patterns) ⟶ (dict, dict)
        if not patterns:
            return files, {}
        kept = {}; dropped = {}
        for path, change in files.items():
            if any(pattern in path for pattern in patterns):
                kept[path] = change
            else:
                dropped[path] = change
        return kept, dropped
    updated = classify_files(selected) ⟶ dict
        return {path: classify_file(change) for path, change in files.items()}
            classify_file(change) ⟶ FileChange
                kind, lang, is_test = classify_path(change.path)
                    lang = detect_lang(path) ⟶ str                 # 叶：按扩展名查表  [纯函数]
                        # .py→python，.tsx→frontend，.sql→sql，.yaml→config，.md→docs，
                        # 表里没有的扩展名返回 other
                    is_test = detect_test(path) ⟶ bool              # 叶：按路径判定  [纯函数]
                        # 任一层目录名为 test/tests/spec/specs/__tests__/testing，或
                        # 文件名主体匹配 test_*、*_test、*.test、*.spec
                    kind = "test" if is_test else lang
                    return kind, lang, is_test
                return change.model_copy(update={"kind": kind, "lang": lang, "is_test": is_test})

    summary = "，".join(f"{change.path}→{change.kind}" for change in updated.values())
    messages = [f"classify：{summary or '没有文件'}"]
    if dropped:
        messages.append(f"classify：按 {patterns} 排除了 {len(dropped)} 个文件：{sorted(dropped)}")
    if patterns and not updated:
        messages.append(f"classify：没有文件路径包含 {patterns}，本次不会审查任何文件")
    return {"files": updated, "history": messages}
    # W: files（覆盖为过滤并打标后的集合）、history
```

## 八 生成意见

```
review_file({file, settings}) ⟶ {"drafts", "history"}                              [网][印]
    # 由 Send 分派，state 参数只含 file 和 settings
    file_change = FileChange.model_validate(state["file"])
    settings = Settings.from_state(state.get("settings"))          # R: 环境变量（补密钥）
    llm = build_chat_model(settings) ⟶ ChatOpenAI
        return ChatOpenAI(model=, base_url=, api_key=, temperature=, timeout=, max_retries=1)
    draft = generate_comments(llm, file_change, settings, attempt=0) ⟶ FileDraft
        if previous:
            system = REVISE_SYSTEM
            user = build_revise_prompt(file_change, previous, failures or [], feedback,
                                       settings.max_comments_per_file) ⟶ str
            # 见第十节
        else:
            system = REVIEW_SYSTEM
            user = build_review_prompt(file_change, settings.max_comments_per_file) ⟶ str
                numbered = render_numbered(file_change) ⟶ str
                    # 叶：把每个 hunk 渲染成「行号 + 符号 + 内容」，最多 400 行
                test_hint = "（这是测试文件，按测试的标准审）" if file_change.is_test else ""
                # 返回：文件路径、改动类型、带行号改动块、
                #       review_profile(kind) 给出的检查重点、条数上限
                # review_profile 取 PROFILES[kind] 拼 COMMON_ISSUES，
                # 表里没有的类型落到 PROFILES["other"]
        result = structured_call(llm, CommentOut, system, user) ⟶ CommentOut
            # 见第九节
        comments = [ReviewComment.from_draft(d) for d in result.comments[:max_comments]]
            # 每条意见在此拿到一个 8 位 id，后续校验、汇总都按 id 关联
        return FileDraft(file_path=file_change.path, kind=file_change.kind,
                         comments=comments, attempt=attempt)
    return {"drafts": {file_change.path: draft},
            **log(f"review_file：{file_change.path} 生成 {len(draft.comments)} 条意见")}
    # W: drafts[本文件路径]、history
    # 多路并发时每路只写自己的键，靠「按文件路径覆盖」的合并规则拼回状态
```

## 九 单次模型调用

```
structured_call(llm, schema, system, user, attempts=3) ⟶ BaseModel                 [网]
    runnable = with_structured_output(llm, schema, method="function_calling")
        # 把「按 schema 回答」包装成一次工具调用，模型回的是工具参数而非自由文本
    messages = [SystemMessage(content=system), HumanMessage(content=user)]
    last_error = None
    for _ in range(max(1, attempts)):
        try:
            result = invoke(runnable, messages) ⟶ BaseModel                        [网]
        except Exception as exc:
            last_error = exc
            continue
        if isinstance(result, schema):
            return result
        try:
            return model_validate(schema, result)
        except Exception as exc:
            last_error = exc
    if last_error is None:
        raise RuntimeError("模型没有返回可解析的结构化结果")
    raise last_error

ping(settings) ⟶ str                                                               [网]
    llm = build_chat_model(settings)
    reply = invoke(llm, [HumanMessage(content="回复两个字：连通")]) ⟶ AIMessage       [网]
    content = reply.content
    if isinstance(content, list):
        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
    return str(content).strip()
```

## 十 校验

```
verify(state) ⟶ {"verdicts", "history"}                                            [网][印]
    drafts = state.get("drafts") or {}
    files = state.get("files") or {}
    settings = Settings.from_state(state.get("settings"))
    llm = build_chat_model(settings)

    def check_one(path) ⟶ (path, list[CommentVerdict])
        R: drafts、files、llm          # 闭包读外层作用域，不出现在自己的签名里
        draft = drafts[path]
        change = files.get(path)
        if change is None or not draft.comments:
            return path, [CommentVerdict(passed=False,
                                         structural_issues=["这个文件不在本次改动里"],
                                         semantic_reason="位置校验未通过，没有做语义判断")
                          for comment in draft.comments]

        structural, pending = split_by_structural_check(change, draft.comments) ⟶ (dict, list)
            failures = {}; pending = []
            for comment in comments:
                issues = check_comment(file_change, comment) ⟶ list[str]           # 见下
                if issues:
                    failures[comment.id] = issues
                else:
                    pending.append(comment)
            return failures, pending

        semantic, error = semantic_checks(llm, change, pending) ⟶ (dict, str)
            if not pending:
                return {}, ""
            payload = [{"id": comment.id, "line_start": str(...), "line_end": str(...),
                        "evidence": comment.evidence, "title": comment.title,
                        "detail": comment.detail} for comment in pending]
            try:
                result = structured_call(llm, SemanticCheckList, VERIFY_SYSTEM,
                                         build_verify_prompt(change, payload)) ⟶ SemanticCheckList   [网]
                    # build_verify_prompt：文件路径 + 该文件带行号改动块 + 每条意见的
                    # id、行号、引用原文、标题、正文；要求逐条回填 comment_id
            except Exception as exc:
                return {}, f"语义判断调用失败：{exc}"
                # 语义判断失败被判为不通过，但不中断整张图
            return {item.comment_id: item for item in result.checks}, ""

        results = []
        for comment in draft.comments:
            issues = structural.get(comment.id)
            if issues:
                verdict = CommentVerdict(passed=False, structural_issues=issues,
                                         semantic_reason="位置校验未通过，没有做语义判断")
            else:
                item = semantic.get(comment.id)
                ok = bool(item and item.supported)
                verdict = CommentVerdict(passed=ok, semantic_ok=ok,
                                         semantic_reason=item.reason if item
                                                         else (error or "模型没有返回这一条的判断"))
            results.append(verdict)
        return path, results

    order = list(drafts)
    verdicts = {}
    workers = max(1, settings.max_concurrency)
    if workers == 1 or len(order) <= 1:
        for path in order:
            key, results = check_one(path)
            verdicts[key] = results
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for key, results in map(pool, check_one, order):        # 线程池并发  [网]
                verdicts[key] = results

    checked = sum(len(group) for group in verdicts.values())
    failed = sum(1 for group in verdicts.values() for verdict in group if not verdict.passed)
    return {"verdicts": verdicts,
            **log(f"verify：第 {round_number + 1} 轮校验 {checked} 条，未通过 {failed} 条")}
    # W: verdicts、history

check_comment(file_change, comment) ⟶ list[str]                                    [纯函数]
    # 位置校验的全部规则，不联网，答案唯一
    issues = []
    if comment.file_path != file_change.path:
        issues.append("文件路径不对：...")
    if comment.line_start < 1 or comment.line_end < comment.line_start:
        issues.append("行号区间不合法：...")
    elif comment.line_end - comment.line_start > MAX_SPAN_LINES:      # MAX_SPAN_LINES = 30
        issues.append("行号区间跨了 N 行，位置不具体，请收窄到 30 行以内")

    window = set(range(comment.line_start, comment.line_end + 1))
    added_numbers = set(file_change.added_lines)
    hit = sorted(window & added_numbers)
    if not added_numbers:
        issues.append("这个文件没有新增行，无法引用")
    elif not hit:
        issues.append("行号 a-b 里没有本次新增的行；本文件的新增行号是 ...")

    evidence_lines = [normalize(line) for line in comment.evidence.splitlines()]
    evidence_lines = [line for line in evidence_lines if line]
    if not evidence_lines:
        issues.append("evidence 是空的，必须逐字引用改动原文")
        return issues

    known = hunk_line_index(file_change) ⟶ set[str]
        # 叶：改动原文里出现过的所有行（含上下文行、被删行）归一化后放进集合
    missing = [line for line in evidence_lines if line not in known]
    if missing:
        issues.append("引用的原文在这个文件的改动里找不到：...")
        return issues

    added_index = added_line_index(file_change) ⟶ dict[str, list[int]]
        # 叶：归一化后的新增行内容 → 出现它的行号
    anchored = False
    for line in evidence_lines:
        for number in added_index.get(line, []):
            if number in window:
                anchored = True
                break
        if anchored:
            break
    if not anchored:
        issues.append("引用的原文和给出的行号对不上：...")
    return issues

normalize(text) ⟶ str                                                              [纯函数]
    return re.sub(r"\s+", " ", text).strip()

route_after_verify(state) ⟶ "revise" | "aggregate"
    settings = Settings.from_state(state.get("settings"))
    round_number = state.get("revision_round", 0)
    if has_failures(state) and round_number < settings.max_rounds:
        return "revise"
    return "aggregate"

has_failures(state) ⟶ bool                                                         [纯函数]
    return any(not verdict.passed for group in verdicts.values() for verdict in group)
```

## 十一 重做

```
revise(state) ⟶ {"drafts", "revision_round", "human_feedback", "history"}          [网][印]
    drafts = state.get("drafts") or {}
    verdicts = state.get("verdicts") or {}
    files = state.get("files") or {}
    feedback = (state.get("human_feedback") or "").strip()
    settings = Settings.from_state(state.get("settings"))
    llm = build_chat_model(settings)
    round_number = state.get("revision_round", 0) + 1

    targets = []
    for path, draft in drafts.items():
        if path not in files:
            continue
        if feedback:
            targets.append((path, [f"人工审查反馈：{feedback}"]))
            continue                              # 人工打回：这个文件整批重做
        by_id = {verdict.comment_id: verdict for verdict in verdicts.get(path, [])}
        failures = [f"{comment.title}（行号 a-b）：{by_id[comment.id].reason_text()}"
                    for comment in draft.comments
                    if comment.id in by_id and not by_id[comment.id].passed]
        if failures:
            targets.append((path, failures))      # 只有存在失败意见的文件才进重做

    def redo(item) ⟶ (path, FileDraft)
        R: drafts、files、llm、settings、feedback、round_number
        path, failures = item
        return path, generate_comments(llm, files[path], settings,
                                       previous=[comment.model_dump()
                                                 for comment in drafts[path].comments],
                                       failures=failures, feedback=feedback,
                                       attempt=round_number) ⟶ FileDraft
            # 见第八节；previous 非空，因此走 REVISE_SYSTEM 与 build_revise_prompt
            # 提示词要求：没通过的要改到能过或删掉，通过的原样保留
            # 注意：模型返回的是这个文件的完整意见列表，代码不逐条比对旧列表

    updated = {}
    workers = max(1, settings.max_concurrency)
    if workers == 1 or len(targets) <= 1:
        for item in targets:
            key, draft = redo(item)
            updated[key] = draft
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for key, draft in map(pool, redo, targets):                    # 线程池并发  [网]
                updated[key] = draft

    mode = "按人工反馈重做" if feedback else "重做未通过的意见"
    return {"drafts": updated, "revision_round": round_number, "human_feedback": "",
            **log(f"revise：第 {round_number} 轮，{mode}，涉及 {len(updated)} 个文件")}
    # W: drafts[被重做的路径]、revision_round、human_feedback（清空）、history
```

## 十二 汇总

```
aggregate(state) ⟶ {"report", "history"}                                           [纯函数]
    drafts = state.get("drafts") or {}
    verdicts = state.get("verdicts") or {}
    files = state.get("files") or {}
    settings = Settings.from_state(state.get("settings"))
    file_reports = []; unresolved = []; by_severity = {"high": 0, "medium": 0, "low": 0}
    total = 0; verified = 0

    for path in sorted(drafts):
        draft = drafts[path]; change = files.get(path)
        by_id = {verdict.comment_id: verdict for verdict in verdicts.get(path, [])}
        comments = []
        for comment in draft.comments:
            verdict = by_id.get(comment.id)
            passed = bool(verdict and verdict.passed)
            total += 1
            by_severity[comment.severity] += 1
            if passed:
                verified += 1
            else:
                issues = list(verdict.structural_issues) if verdict else ["没有校验结论"]
                if verdict and verdict.semantic_reason:
                    issues.append(verdict.semantic_reason)
                unresolved.append(UnresolvedItem(file_path=path, title=comment.title,
                                                 line_start=..., line_end=..., issues=issues))
                # 到轮次上限仍未通过的意见不丢弃，单独进报告的「未通过校验」一节
            comments.append(CommentReport(id=..., line_start=..., line_end=...,
                                          severity=..., category=..., title=...,
                                          detail=..., evidence=..., verified=passed))
        sort(comments, key=(SEVERITY_ORDER[severity], line_start))    # severity 高的在前
        file_reports.append(FileReport(path=path, kind=draft.kind,
                                       lang=change.lang if change else "other",
                                       change_type=change.change_type if change else "modified",
                                       comments=comments))

    report = Report(model=settings.model, source=state.get("source", ""),
                    stats=ReportStats(files_reviewed=len(file_reports), comments_total=total,
                                      comments_verified=verified, by_severity=by_severity,
                                      rounds=state.get("revision_round", 0)),
                    files=file_reports, unresolved=unresolved)
    return {"report": report.model_dump(),
            **log(f"aggregate：{total} 条意见，通过校验 {verified} 条，待人工判断 {total - verified} 条")}
    # W: report、history
```

## 十三 人工确认与落盘

```
human_gate(state) ⟶ {"decision", "human_feedback", "revision_round", "history"}     [印]
    payload = {"question": "报告已生成，选择下一步",
               "choices": ["approve", "revise", "abort"],
               "report": state.get("report") or {}}
    answer = interrupt(payload) ⟶ 人的选择
        # 图在此原地暂停，状态存入 checkpointer，进程可以退出；
        # 恢复时本节点从头重跑，因此 interrupt 之前不能有副作用

    if isinstance(answer, dict):
        choice = str(answer.get("decision", "")).strip().lower()
        feedback = str(answer.get("feedback", "")).strip()
    else:
        choice = str(answer).strip().lower()
        feedback = ""
    if choice not in CHOICES:
        choice, feedback = "abort", f"收到无法识别的选择 {choice!r}，按放弃处理"
    return {"decision": choice, "human_feedback": feedback, "revision_round": 0, ...}
    # W: decision、human_feedback、revision_round（归零，让打回后重新获得完整重做轮次）

route_after_human_gate(state) ⟶ "finalize" | "revise" | END
    decision = state.get("decision", "abort")
    if decision == "approve":
        return "finalize"
    if decision == "revise":
        return "revise"
    return END                                   # 放弃：交给框架结束流程，不写报告

finalize(state) ⟶ {"output_paths", "history"}                                       [文][印]
    report = Report.model_validate(state["report"] or {})
    output_dir = Path(state.get("output_dir") or "out")
    mkdir(output_dir, parents=True, exist_ok=True) ⟶ None                            [文]
    json_path = output_dir / "report.json"
    markdown_path = output_dir / "report.md"
    write_text(json_path, report.model_dump_json(indent=2), encoding="utf-8")        [文]
    write_text(markdown_path, report.to_markdown(), encoding="utf-8")                [文]
    return {"output_paths": [str(json_path), str(markdown_path)], ...}
    # W: output_paths、history
```

## 十四 其他子命令

```
doctor(model, base_url)
    settings = load_settings(model=, base_url=) ⟶ Settings
    print(接口地址 / 模型名 / 密钥是否配置)                                            [印]
    try:
        print(f"文本调用：{ping(settings)}")                                          [印][网]
    except Exception as exc:
        print(f"文本调用失败：{exc}")                                                 [印]
        raise Exit(code=1)
    sample = "    1 + def div(a, b):\n    2 +     return a / b"
    try:
        result = structured_call(build_chat_model(settings), CommentOut, 系统提示, 用户提示)
                 ⟶ CommentOut                                                        [网]
    except Exception as exc:
        print(f"结构化输出失败：{exc}")                                               [印]
        raise Exit(code=1)
    print(返回条数；若有则打印首条的路径、行号、级别、标题、引用原文)                        [印]
    print("自检通过")                                                                [印]

checkpoint(kind, db)
    saver = make_checkpointer(kind, db) ⟶ BaseCheckpointSaver                        [存]
    print(检查点类型、文件名)                                                         [印]

graph()
    compiled = build_graph() ⟶ CompiledStateGraph
    print(draw_mermaid(get_graph(compiled)))                                         [印]

ingest(from_git, patch, diff_text, numbered)
    source = _resolve_source(from_git, patch, diff_text) ⟶ dict
    state = ingest_node({"diff_source": source}) ⟶ dict                              [进][文]
    changes = parse_unified_diff(state["diff_text"]) ⟶ list[FileChange]
    print(来源、解析摘要)                                                             [印]
    for change in changes:
        print(路径、改动类型、新增行数、前十个新增行号)                                    [印]
    if numbered:
        for change in changes:
            print(Rule(change.path))                                                 [印]
            print(render_numbered(change))                                           [印]

show(report, min_severity, only_file, unverified_only)
    threshold = SEVERITY_ORDER.get(min_severity)
    if threshold is None:
        print("不认识的级别：...")                                                    [印]
        raise Exit(code=1)
    path = Path(report)
    if not is_file(path):
        print("找不到报告文件：...")                                                  [印]
        raise Exit(code=1)
    parsed = model_validate_json(Report, read_text(path)) ⟶ Report                   [文]
    shown = 0
    for file_report in parsed.files:
        if only_file and file_report.path != only_file:
            continue
        picked = [comment for comment in file_report.comments
                  if SEVERITY_ORDER.get(comment.severity, 0) >= threshold
                  and (comment.verified or not unverified_only)]
        if not picked:
            continue
        print(文件路径、类型、条数)                                                    [印]
        for comment in picked:
            print(行号、级别、标题、未通过标记)                                          [印]
        shown += len(picked)
    print(f"共筛出 {shown} 条，报告共 {parsed.stats.comments_total} 条")               [印]

resume(thread, db, auto_approve)
    settings = load_settings() ⟶ Settings
    config = {"configurable": {"thread_id": thread}, "max_concurrency": ...}
    compiled = build_graph(make_checkpointer("sqlite", db))                         [存]
    snapshot = get_state(compiled, config) ⟶ StateSnapshot                          [存]
    if not snapshot or not snapshot.values:
        print(f"检查点 {db} 里没有会话 {thread}")                                     [印]
        raise Exit(code=1)
    if _first_interrupt(snapshot) is None:
        print(f"会话 {thread} 没有停在人工确认上")                                     [印]
        raise Exit(code=1)
    print(f"会话 {thread}｜从 {db} 恢复")                                            [印]
    payload = Command(resume={"decision": "approve"}) if auto_approve else None
    # payload 为 None 时框架从检查点重新执行被中断的 human_gate
    values = _drive(compiled, payload, config, auto_approve, None) ⟶ dict
    print(结果与输出路径)                                                             [印]
```

## 十五 数据注入链

### 15.1 改动文本进入审查提示词

```
来源：git diff 输出 / patch 文件 / --diff-text                           [进][文]
    │
    │ ingest 写入 state.diff_text
    ▼
state.diff_text
    │
    │ parse_diff → parse_unified_diff
    ▼
FileChange.hunks（原始行，带 + - 空格前缀）
FileChange.added_lines = {新文件行号 → 行内容}       ← 后续所有行号校验的唯一依据
    │
    │ 过滤：只取被 Send 分派的那一个文件
    ▼
render_numbered(file_change, max_lines=400) ⟶ str
    │ 行号右对齐 + 符号 + 内容；超过 400 行截断并标注
    ▼
build_review_prompt(file_change, max_comments) ⟶ str
    │ │
    │ ├─ 注入位置 1：文件路径、改动类型、是否测试文件
    │ ├─ 注入位置 2：提示词中围栏内的带行号改动块（上面 render_numbered 的结果）
    │ └─ 注入位置 3：review_profile(kind) —— 该类型的检查重点 + 通用检查方向
    ▼
structured_call(llm, CommentOut, REVIEW_SYSTEM, user)                  [网]
    │ 以名为 CommentOut 的工具形式下发结构约束
    ▼
模型 → CommentOut.comments（file_path, line_start, line_end, severity,
                            category, title, detail, evidence）
```

### 15.2 意见进入校验提示词

```
CommentOut.comments
    │ ReviewComment.from_draft 逐条转换，补上 8 位 id
    ▼
FileDraft.comments ──写入──► state.drafts[文件路径]          # W，按文件路径覆盖
    │
    │ split_by_structural_check → check_comment
    ├──────────────► 位置校验不过：structural[comment.id] = 失败原因
    │                    │
    │                    ▼
    │                不经模型，直接进 verdicts，passed=False
    │ 位置校验通过：pending
    ▼
semantic_checks(llm, file_change, pending)
    │ 组装 payload：每条取 id、line_start、line_end、evidence、title、detail
    ▼
build_verify_prompt(file_change, payload) ⟶ str
    │ │
    │ ├─ 注入位置 1：文件路径
    │ ├─ 注入位置 2：该文件带行号改动块（再次调用 render_numbered）
    │ └─ 注入位置 3：逐条意见的 id、行号、引用原文、标题、正文
    ▼
structured_call(llm, SemanticCheckList, VERIFY_SYSTEM, user)           [网]
    ▼
SemanticCheckList.checks → {comment_id → SemanticCheck(supported, reason)}
    │
    ▼
CommentVerdict ──写入──► state.verdicts[文件路径]            # W，按文件路径覆盖
```

### 15.3 校验结论进入重做提示词

```
state.verdicts[文件路径] + state.drafts[文件路径] + state.human_feedback
    │
    │ revise：按文件聚合
    ├── 人工打回（feedback 非空）→ failures = ["人工审查反馈：..."]，该文件整批重做
    └── 自动重做 → failures = ["标题（行号 a-b）：失败原因"，...]
                    失败原因来自 CommentVerdict.reason_text()
                    位置类原因直接给出「本文件的新增行号是 ...」
    ▼
build_revise_prompt(file_change, previous, failures, feedback, max_comments) ⟶ str
    │ previous = 上一轮这个文件的完整意见列表（id、标题、行号、引用原文、正文）
    │ │
    │ ├─ 注入位置 1：文件路径、改动类型
    │ ├─ 注入位置 2：带行号改动块
    │ ├─ 注入位置 3：上一轮完整意见列表
    │ ├─ 注入位置 4：未通过校验的意见及原因（有失败时）
    │ ├─ 注入位置 5：人工审查反馈（人工打回时）
    │ └─ 注入位置 6：review_profile(kind)
    ▼
structured_call(llm, CommentOut, REVISE_SYSTEM, user)                  [网]
    ▼
CommentOut.comments → 覆盖 state.drafts[文件路径]，回到 verify
```

### 15.4 配置与密钥进入每次模型调用

```
.env 文件 / 环境变量                                            [文]
    │ load_settings → load_dotenv + getenv
    ▼
Settings（含 api_key）
    │
    │ Settings.for_state() 弹出 api_key
    ▼
state.settings（不含密钥）──► 存入检查点，落盘的内容里没有密钥            [存][文]
    │
    │ 每个要模型的节点：Settings.from_state(state.settings)
    │ 发现 api_key 为空 → resolve_api_key() 从环境变量补回
    ▼
build_chat_model(settings) ⟶ ChatOpenAI(model, base_url, api_key, temperature, timeout)
    ▼
structured_call(...) → invoke(...)                                     [网]
```

### 15.5 结果进入报告文件

```
state.drafts（每个文件的意见，按路径）
state.verdicts（每条意见的通过与否和原因，按路径）
state.files（每个文件的类型、改动类型）
state.revision_round、state.source
    │
    ▼
aggregate(state)
    │ 逐文件逐条比对 drafts 与 verdicts，按 id 关联
    │
    ├─► FileReport.comments（每条带 verified 标记，按 severity 排序）
    └─► Report.unresolved（到上限仍未通过的条目，附失败原因）
    ▼
state.report
    │
    ▼
finalize(state)
    │ ├─ report.json：Report.model_dump_json(indent=2)
    │ └─ report.md：Report.to_markdown()
    ▼
out/report.json、out/report.md                                         [文]
```

### 15.6 人的选择进入图

```
stdin 输入（_interactive_decision）或命令行参数（--auto-approve / --reject-first）
    │
    ▼
Command(resume={"decision": ..., "feedback": ...})
    │ 由 _drive 作为下一轮 stream 的输入
    ▼
interrupt(payload) 的返回值 → human_gate 内的 answer
    │
    ├─ decision 写入 state.decision ──► route_after_human_gate 读它决定去向
    ├─ feedback 写入 state.human_feedback ──► revise 读它触发整批重做
    └─ revision_round 归零 ──► 打回后重新获得完整的自动重做轮次
```
