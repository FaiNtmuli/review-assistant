# 调用链：一次 review run 的完整执行

用例：

```
review run --patch samples/demo.diff --out out_reject ^
           --reject-first "少提一条：缺少对 records 为空时的处理，请补上" --auto-approve
```

选它的原因：这一次运行把两条回头环都走到了。自动重做环走了一轮（校验有 1 条没通过），
人工打回环走了一轮（第一次人工确认选了打回并带反馈）。`--reject-first` 等价于在第一次
人工确认时选 r 并输入那段反馈，`--auto-approve` 等价于第二次确认时选 a。

画法约定：

- 树形缩进表示函数的展开关系。被调用的函数写在调用点下方并缩进一级，展开后的函数体再缩进一级。
- 控制结构按源码缩进写，不额外增加树的层级。
- `⟶ result` 表示返回值绑定到 result；无返回值或忽略返回值时只写调用。
- `R:` 读取边界之外已有的数据，`W:` 修改边界之外已有的状态。
- 副作用：`[网]` 网络调用，`[印]` 输出，`[进]` 进程或命令，`[文]` 文件读写，`[存]` 持久化。
- 同一个函数第二次出现时不再展开，作为叶节点保留调用和结果，并注明它走的是哪个分支。
- 框架内部（typer、rich、pydantic、langgraph 的调度实现、openai 客户端）不展开。
- 会话号、密钥等取自真实运行，密钥一律写成 `sk-...`。

## 一 调用链

```
main()                                                                  # 进程入口
    app()                                                               # typer 解析参数并分发  [印]
        run(from_git=None, patch="samples/demo.diff", diff_text=None,
            out="out_reject", kind="memory", db="checkpoints.db", thread=None, paths=None,
            model=None, base_url=None, max_concurrency=4, max_rounds=3,
            auto_approve=True, pause_at_review=False,
            reject_first="少提一条：缺少对 records 为空时的处理，请补上")
            source = _resolve_source(None, "samples/demo.diff", None) ⟶ {"type": "patch", "path": "samples/demo.diff"}
                given = [value for value in (None, "samples/demo.diff", None) if value] ⟶ ["samples/demo.diff"]
                if len(given) != 1:                        # 1 == 1，不成立
                    raise BadParameter("改动来源三选一：--from-git 或 --patch 或 --diff-text")
                if from_git:                               # 不成立
                    return {"type": "git", "range": from_git}
                if patch:                                  # 成立
                    return {"type": "patch", "path": "samples/demo.diff"}
                return {"type": "text", "text": diff_text or ""}

            path_filter = parse_paths(None) ⟶ []
                if not raw:                                # 成立
                    return []
                return [part.strip() for part in raw.split(",") if part.strip()]

            settings = load_settings(model=None, base_url=None,
                                     max_concurrency=4, max_rounds=3) ⟶ Settings
                load_dotenv(None, override=False)                                  [文]
                R: 环境变量、.env 文件
                resolved = {
                    "model": getenv("REVIEW_MODEL", "deepseek-chat") ⟶ "deepseek-chat",
                    "base_url": getenv("REVIEW_BASE_URL", "https://api.deepseek.com/v1")
                                ⟶ "https://api.deepseek.com/v1",
                    "api_key": resolve_api_key() ⟶ "sk-...",
                        return getenv("REVIEW_API_KEY") or getenv("DEEPSEEK_API_KEY")
                               or getenv("OPENAI_API_KEY") or "not-needed"
                        # DEEPSEEK_API_KEY 命中，返回它
                    "temperature": float(getenv("REVIEW_TEMPERATURE", "0")) ⟶ 0.0,
                    "max_concurrency": int(getenv("REVIEW_MAX_CONCURRENCY", "4")) ⟶ 4,
                    "max_rounds": int(getenv("REVIEW_MAX_ROUNDS", "3")) ⟶ 3,
                }
                for key, value in overrides.items():
                    if value is not None:
                        resolved[key] = value              # 命令行的 4 和 3 覆盖同名的环境变量值
                return Settings(**resolved) ⟶ Settings(model="deepseek-chat", temperature=0.0,
                                                       timeout=180.0, max_comments_per_file=8, ...)

            thread_id = None or uuid4().hex[:12] ⟶ "2699f40ae94f"
            config = {"configurable": {"thread_id": "2699f40ae94f"}, "max_concurrency": 4}

            print("会话 2699f40ae94f｜模型 deepseek-chat｜检查点 memory｜并发 4")               [印]
            if path_filter:                                # [] 为假，不成立，跳过
                print("只审查路径里包含 [] 的文件")

            compiled = build_graph(make_checkpointer("memory", "checkpoints.db")) ⟶ CompiledStateGraph
                make_checkpointer("memory", "checkpoints.db") ⟶ InMemorySaver
                    if kind == "memory":                   # 成立
                        return InMemorySaver(serde=make_serde())
                            make_serde() ⟶ JsonPlusSerializer
                                # 叶：把状态里的六个自定义类型登记进序列化器白名单
                    if kind == "sqlite":                   # 不成立
                        ...
                build_graph(saver=None) ⟶ CompiledStateGraph
                    builder = StateGraph(ReviewState)
                    add_node(builder, "ingest", ingest)    # 下面九行逐个登记节点函数
                    add_node(builder, "parse_diff", parse_diff)
                    add_node(builder, "classify", classify)
                    add_node(builder, "review_file", review_file)
                    add_node(builder, "verify", verify)
                    add_node(builder, "revise", revise)
                    add_node(builder, "aggregate", aggregate)
                    add_node(builder, "human_gate", human_gate)
                    add_node(builder, "finalize", finalize)
                    add_edge(builder, START, "ingest")                     # 主线
                    add_edge(builder, "ingest", "parse_diff")
                    add_edge(builder, "parse_diff", "classify")
                    add_conditional_edges(builder, "classify", fan_out_files,
                                          ["review_file", "aggregate"])    # 分叉
                    add_edge(builder, "review_file", "verify")             # 汇合
                    add_conditional_edges(builder, "verify", route_after_verify,
                                          ["revise", "aggregate"])         # 重试环的出口
                    add_edge(builder, "revise", "verify")                  # 重试环的回边
                    add_edge(builder, "aggregate", "human_gate")
                    add_conditional_edges(builder, "human_gate", route_after_human_gate,
                                          ["finalize", "revise", END])     # 人工打回的出口
                    add_edge(builder, "finalize", END)
                    return compile(builder, checkpointer=saver) ⟶ CompiledStateGraph

            initial = {"diff_source": {"type": "patch", "path": "samples/demo.diff"},
                       "output_dir": "out_reject",
                       "path_filter": [],
                       "settings": settings.for_state() ⟶ {...},       # 弹出的那份不含 api_key
                           data = model_dump()
                           data.pop("api_key", None)                   # 密钥不进状态，检查点会落盘
                           return data
                       "drafts": {}, "verdicts": {},
                       "revision_round": 0, "human_feedback": "", "history": []}
            W: 这是整张图的初始状态，之后每个节点都在它之上累积

            if pause_at_review:                            # False，跳过
                print(f"恢复命令：review resume --thread {thread_id} --db {db} --auto-approve")

            values = _drive(compiled, initial, config, True,
                            "少提一条：缺少对 records 为空时的处理，请补上", False) ⟶ dict

                rejected = False
                while True:                                # ── 第 1 轮 ──
                    for chunk in stream(initial, config, stream_mode="updates"):
                        # stream 由 LangGraph 驱动整张图：按连线顺序调用下列节点，每完成
                        # 一个节点产出一个 chunk。节点之间不直接互调，都通过共享状态交换
                        # 数据；chunk 回到本循环体打印一条 history，调用与打印是交错的。
                        ingest(state) ⟶ {"diff_text": ..., "source": "patch samples\\demo.diff",
                                         "history": ["ingest：读入 patch samples\\demo.diff，82 行 diff"]}
                            source = state.get("diff_source") ⟶ {"type": "patch", "path": "samples/demo.diff"}
                            kind = source.get("type", "git") ⟶ "patch"
                            if kind == "git":              # 不成立，跳过
                                ...
                            elif kind == "patch":          # 成立
                                path = Path(source.get("path", "")) ⟶ WindowsPath("samples/demo.diff")
                                if not path.is_file():     # 文件存在，不成立
                                    raise FileNotFoundError(f"找不到改动文件：{path}")
                                text = path.read_text(encoding="utf-8", errors="replace") ⟶ str
                                    R: 磁盘上的 patch 文件                                    [文]
                                label = f"patch {path}" ⟶ "patch samples\\demo.diff"
                            if not text.strip():           # 有内容，不成立
                                raise ValueError(f"{label} 里没有任何改动")
                            return {"diff_text": text, "source": label,
                                    **log(f"ingest：读入 {label}，{len(text.splitlines())} 行 diff")}
                            W: diff_text、source、history

                        parse_diff(state) ⟶ {"files": {...3 个文件...},
                                             "history": ["parse_diff：3 个文件，35 行新增"]}
                            changes = parse_unified_diff(state.get("diff_text", "")) ⟶ list[FileChange]
                                # 叶：逐行扫描 diff 文本，遇 "diff --git" 开新文件、遇 "@@" 开新
                                # hunk，其余按首字符 +、-、空格 归入新增行、删除行、上下文行。
                                # 新增行记为 {新文件行号: 行内容}，这是行号校验的唯一依据。
                                # 内部闭包 flush() 在文件切换时收尾，它的 W: files、current
                            mapping = {change.path: change for change in changes if change.path}
                            added = sum(len(change.added_lines) for change in mapping.values()) ⟶ 35
                            return {"files": mapping, **log(...)}
                            W: files、history

                        classify(state) ⟶ {"files": {... kind 已填 ...},
                                           "history": ["classify：app/service.py→python，tests/test_service.py→test，config/prod.yaml→config"]}
                            files = state.get("files") ⟶ {3 个文件}
                            patterns = state.get("path_filter") or [] ⟶ []
                            selected, dropped = filter_paths(files, patterns) ⟶ (3 个文件, {})
                                if not patterns:           # 成立
                                    return files, {}
                                # 后面的循环本次未执行
                            updated = classify_files(selected) ⟶ {...3 个文件，kind 已填...}
                                return {path: classify_file(change) for path, change in files.items()}
                                    classify_file(change) ⟶ FileChange          # 每个文件各调用一次
                                        kind, lang, is_test = classify_path(change.path)
                                            lang = detect_lang(path) ⟶ "python"
                                                # 叶：按扩展名查表，.py→python，.tsx→frontend，
                                                # .sql→sql，.yaml→config，.md→docs，表外为 other
                                            is_test = detect_test(path) ⟶ False
                                                # 叶：任一层目录名为 test/tests/spec/specs/
                                                # __tests__/testing，或文件名主体匹配
                                                # test_*、*_test、*.test、*.spec
                                            kind = "test" if is_test else lang ⟶ "python"
                                            return kind, lang, is_test
                                        return change.model_copy(update={"kind": "python",
                                                                         "lang": "python",
                                                                         "is_test": False})
                            summary = "，".join(...) ⟶ "app/service.py→python，tests/test_service.py→test，config/prod.yaml→config"
                            messages = [f"classify：{summary or '没有文件'}"]
                            if dropped:                    # 空字典，不成立
                                messages.append(...)
                            if patterns and not updated:   # [] 为假，不成立
                                messages.append(...)
                            return {"files": updated, "history": messages}
                            W: files（覆盖为过滤并打标后的集合）、history

                        fan_out_files(state) ⟶ [Send("review_file", {...}), Send(...), Send(...)]
                            files = state.get("files") ⟶ {3 个文件}
                            if not files:                  # 不成立
                                return "aggregate"
                            payload_settings = state.get("settings") ⟶ {...不含 api_key...}
                            return [Send("review_file", {"file": change.model_dump(),
                                                         "settings": payload_settings})
                                    for change in files.values()
                                    if not change.is_binary] or "aggregate"
                            # 三个文件都不是二进制，得到三条分派指令

                        review_file({"file": <app/service.py>, "settings": {...}}) ⟶ {"drafts": {...}, "history": [...]}
                            # 三路并发：这一路展开，另两路见下方叶节点
                            # 被 Send 分派，入口参数只含 file 和 settings，看不到共享状态的其他字段
                            file_change = model_validate(FileChange, state["file"]) ⟶ FileChange
                            settings = Settings.from_state(state.get("settings")) ⟶ Settings
                                settings = cls(**(data or {}))
                                if not settings.api_key:            # 为空，成立
                                    settings.api_key = resolve_api_key() ⟶ "sk-..."
                                        R: 环境变量
                                return settings
                            llm = build_chat_model(settings) ⟶ ChatOpenAI
                                # 叶：ChatOpenAI(model=, base_url=, api_key=, temperature=,
                                #     timeout=, max_retries=1)，只构造客户端，不发起请求
                            draft = generate_comments(llm, file_change, settings, attempt=0) ⟶ FileDraft
                                if previous:                   # None，不成立
                                    system = REVISE_SYSTEM
                                    user = build_revise_prompt(...)
                                else:                          # 成立
                                    system = REVIEW_SYSTEM
                                    user = build_review_prompt(file_change, 8) ⟶ str
                                        numbered = render_numbered(file_change) ⟶ str
                                            # 叶：每个 hunk 渲染成「行号 + 符号 + 内容」，
                                            # 最多 400 行，超出则截断并标注
                                        test_hint = ""          # is_test 为 False
                                        return 拼好的提示词 ⟶ str
                                            # 内容依次是：文件路径与改动类型、围栏内的带行号改动块
                                            # （即上面的 numbered）、检查重点、条数上限、
                                            # 以及「evidence 必须逐字复制」的书写要求
                                            review_profile(kind) ⟶ str
                                                kind = "python"
                                                # 叶：PROFILES["python"] 拼接 COMMON_ISSUES，
                                                # 表里没有的类型落到 PROFILES["other"]
                                result = structured_call(llm, CommentOut, system, user) ⟶ CommentOut
                                    # 这一次首次调用，展开；后续出现均为叶节点
                                    runnable = with_structured_output(llm, CommentOut,
                                                                      method="function_calling")
                                        # 把「按 CommentOut 回答」包装成一个同名工具随请求下发
                                    messages = [SystemMessage(content=system),
                                                HumanMessage(content=user)]
                                    last_error = None
                                    for _ in range(max(1, 3)):          # 首次即成功
                                        try:
                                            result = invoke(runnable, messages) ⟶ CommentOut        [网]
                                                # 叶：一次工具调用请求，返回 CommentOut 实例
                                        except Exception as exc:
                                            last_error = exc
                                            continue
                                        if isinstance(result, CommentOut):  # 成立
                                            return result
                                        try:
                                            return model_validate(CommentOut, result)
                                        except Exception as exc:
                                            last_error = exc
                                    if last_error is None:
                                        raise RuntimeError("模型没有返回可解析的结构化结果")
                                    raise last_error
                                comments = [ReviewComment.from_draft(draft)
                                            for draft in result.comments[:8]] ⟶ list[ReviewComment]
                                    # 每条在此拿到一个 8 位 id，后续校验、汇总都按 id 关联
                                return FileDraft(file_path="app/service.py", kind="python",
                                                 comments=comments, attempt=0)
                            return {"drafts": {"app/service.py": draft},
                                    **log("review_file：app/service.py 生成 7 条意见")}
                            W: drafts["app/service.py"]、history

                        review_file({"file": <tests/test_service.py>, ...}) ⟶ {...}
                            # 叶：不展开，与上一路走同一条路径，实际生成 2 条意见
                            # 三路由框架并发调度，各自只写自己那个键，靠「按文件路径覆盖」
                            # 的合并规则拼回状态，互不覆盖
                        review_file({"file": <config/prod.yaml>, ...}) ⟶ {...}
                            # 叶：不展开，同上，实际生成 3 条意见

                        verify(state) ⟶ {"verdicts": {...},
                                         "history": ["verify：第 1 轮校验 12 条，未通过 1 条"]}
                            # 这一次首次调用，展开；后续出现均为叶节点
                            drafts = state.get("drafts") ⟶ {3 个文件的草稿}
                            files = state.get("files") ⟶ {3 个文件}
                            settings = Settings.from_state(state.get("settings")) ⟶ Settings
                            llm = build_chat_model(settings) ⟶ ChatOpenAI

                            check_one(path) ⟶ (path, list[CommentVerdict])      # 每个文件各调用一次
                                R: drafts、files、llm          # 闭包读外层作用域，不在自己的签名里
                                draft = drafts[path]
                                change = files.get(path)
                                if change is None or not draft.comments:
                                    # app/service.py 有 7 条意见且文件存在，不成立
                                    reason = "这个文件不在本次改动里"
                                    return path, [CommentVerdict(passed=False, ...)]

                                structural, pending = split_by_structural_check(change, draft.comments) ⟶ (dict, list)
                                    failures = {}; pending = []
                                    for comment in comments:        # 7 条逐条判
                                        issues = check_comment(file_change, comment) ⟶ list[str]
                                            issues = []
                                            if comment.file_path != file_change.path:   # 相等，不成立
                                                issues.append("文件路径不对：...")
                                            if comment.line_start < 1 or comment.line_end < comment.line_start:
                                                # 不成立
                                                issues.append("行号区间不合法：...")
                                            elif comment.line_end - comment.line_start > 30:
                                                # 不成立
                                                issues.append("行号区间跨了 N 行，位置不具体...")
                                            window = set(range(comment.line_start, comment.line_end + 1))
                                            added_numbers = set(file_change.added_lines)
                                            hit = sorted(window & added_numbers)
                                            if not added_numbers:       # 24 行，不成立
                                                issues.append("这个文件没有新增行，无法引用")
                                            elif not hit:               # 命中，不成立
                                                issues.append("行号 a-b 里没有本次新增的行；...")
                                            evidence_lines = [normalize(line)
                                                              for line in comment.evidence.splitlines()]
                                            evidence_lines = [line for line in evidence_lines if line]
                                            if not evidence_lines:      # 不成立
                                                issues.append("evidence 是空的...")
                                                return issues
                                            known = hunk_line_index(file_change) ⟶ set[str]
                                                # 叶：改动原文里出现过的所有行（含上下文行、
                                                # 被删行）归一化后放进集合
                                            missing = [line for line in evidence_lines
                                                       if line not in known]
                                            if missing:                 # 不成立
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
                                            if not anchored:            # 不成立
                                                issues.append("引用的原文和给出的行号对不上：...")
                                            return issues
                                        normalize(text) ⟶ str            # 被 check_comment 反复调用
                                            return re.sub(r"\s+", " ", text).strip()
                                        if issues:
                                            failures[comment.id] = issues
                                        else:
                                            pending.append(comment)
                                    return failures, pending
                                    # 本轮 7 条里 6 条位置过关，1 条失败

                                semantic, error = semantic_checks(llm, change, pending) ⟶ (dict, str)
                                    if not pending:                 # 6 条，不成立
                                        return {}, ""
                                    payload = [{"id": ..., "line_start": "8", "line_end": "9",
                                                "evidence": ..., "title": ..., "detail": ...}
                                               for comment in pending]
                                    try:
                                        result = structured_call(llm, SemanticCheckList,
                                                                 VERIFY_SYSTEM,
                                                                 build_verify_prompt(change, payload))
                                                 ⟶ SemanticCheckList                          [网]
                                            # 叶：同一次工具调用路径
                                            # build_verify_prompt 拼入：文件路径、该文件带行号
                                            # 改动块、逐条意见的 id、行号、引用原文、标题、正文
                                    except Exception as exc:
                                        return {}, f"语义判断调用失败：{exc}"
                                        # 语义判断失败记为不通过，但不中断整张图
                                    return {item.comment_id: item for item in result.checks}, ""

                                results = []
                                for comment in draft.comments:      # 7 条逐条定结论
                                    issues = structural.get(comment.id)
                                    if issues:                      # 1 条命中
                                        verdict = CommentVerdict(comment_id=..., file_path="app/service.py",
                                                                 passed=False,
                                                                 structural_issues=issues,
                                                                 semantic_reason="位置校验未通过，没有做语义判断")
                                    else:                           # 6 条走这里
                                        item = semantic.get(comment.id)
                                        ok = bool(item and item.supported)
                                        verdict = CommentVerdict(comment_id=..., file_path="app/service.py",
                                                                 passed=ok, semantic_ok=ok,
                                                                 semantic_reason=item.reason if item
                                                                 else (error or "模型没有返回这一条的判断"))
                                    results.append(verdict)
                                return path, results
                                # 注：位置不过关的那条不消耗模型调用，直接带失败原因进结论

                            order = list(drafts) ⟶ ["app/service.py", "tests/test_service.py", "config/prod.yaml"]
                            verdicts = {}
                            workers = max(1, settings.max_concurrency) ⟶ 4
                            if workers == 1 or len(order) <= 1:      # 不成立
                                for path in order:
                                    key, results = check_one(path)
                                    verdicts[key] = results
                            else:                                    # 成立
                                with ThreadPoolExecutor(max_workers=4) as pool:
                                    for key, results in map(pool, check_one, order):
                                        verdicts[key] = results
                                        # 叶：三个文件的校验在线程池里并发，内部各自发起 [网]
                            checked = sum(len(group) for group in verdicts.values()) ⟶ 12
                            failed = sum(1 for group in verdicts.values()
                                         for verdict in group if not verdict.passed) ⟶ 1
                            round_number = state.get("revision_round", 0) ⟶ 0
                            return {"verdicts": verdicts,
                                    **log(f"verify：第 {round_number + 1} 轮校验 {checked} 条，"
                                         f"未通过 {failed} 条")}
                            W: verdicts、history

                        route_after_verify(state) ⟶ "revise"
                            settings = Settings.from_state(state.get("settings")) ⟶ Settings
                            round_number = state.get("revision_round", 0) ⟶ 0
                            if has_failures(state) and round_number < settings.max_rounds:
                                # 有 1 条没通过，且 0 < 3，成立
                                return "revise"
                            return "aggregate"

                        revise(state) ⟶ {"drafts": {...}, "revision_round": 1,
                                         "human_feedback": "",
                                         "history": ["revise：第 1 轮，重做未通过的意见，涉及 1 个文件"]}
                            # 这一次首次调用，展开；后续出现均为叶节点
                            drafts = state.get("drafts") ⟶ {3 个文件}
                            verdicts = state.get("verdicts") ⟶ {3 个文件}
                            files = state.get("files") ⟶ {3 个文件}
                            feedback = (state.get("human_feedback") or "").strip() ⟶ ""
                            settings = Settings.from_state(state.get("settings")) ⟶ Settings
                            llm = build_chat_model(settings) ⟶ ChatOpenAI
                            round_number = state.get("revision_round", 0) + 1 ⟶ 1

                            targets = []
                            for path, draft in drafts.items():          # 三个文件逐个看
                                if path not in files:                   # 都在，不成立
                                    continue
                                if feedback:                            # 空串，不成立
                                    targets.append((path, [f"人工审查反馈：{feedback}"]))
                                    continue
                                by_id = {verdict.comment_id: verdict
                                         for verdict in verdicts.get(path, [])}
                                failures = [f"{comment.title}（行号 a-b）：{by_id[comment.id].reason_text()}"
                                            for comment in draft.comments
                                            if comment.id in by_id and not by_id[comment.id].passed]
                                if failures:                            # 只有 app/service.py 命中
                                    targets.append((path, failures))
                            # W: targets（本次只有 app/service.py，即"只重做有失败意见的文件"）

                            redo(item) ⟶ (path, FileDraft)
                                R: drafts、files、llm、settings、feedback、round_number
                                path, failures = item ⟶ ("app/service.py", ["可变默认参数...：引用的原文和给出的行号对不上：..."])
                                return path, generate_comments(llm, files[path], settings,
                                                               previous=[comment.model_dump()
                                                                         for comment in drafts[path].comments],
                                                               failures=failures, feedback="",
                                                               attempt=1) ⟶ FileDraft
                                    # 不展开：previous 非空，因此走 REVISE_SYSTEM 与
                                    # build_revise_prompt，后者拼入改动原文、上一轮这个文件的完整
                                    # 意见列表、未通过的原因、review_profile(kind)
                                    # 模型返回的是该文件的完整意见列表，代码不逐条比对旧列表

                            updated = {}
                            workers = max(1, settings.max_concurrency) ⟶ 4
                            if workers == 1 or len(targets) <= 1:       # len 为 1，成立
                                for item in targets:
                                    key, draft = redo(item)
                                    updated[key] = draft
                            else:
                                with ThreadPoolExecutor(max_workers=workers) as pool:
                                    for key, draft in map(pool, redo, targets):
                                        updated[key] = draft
                            mode = "按人工反馈重做" if feedback else "重做未通过的意见" ⟶ "重做未通过的意见"
                            return {"drafts": updated, "revision_round": 1,
                                    "human_feedback": "", **log(...)}
                            W: drafts["app/service.py"]、revision_round、human_feedback（清空）、history

                        verify(state) ⟶ {"verdicts": {...},
                                         "history": ["verify：第 2 轮校验 11 条，未通过 0 条"]}
                            # 叶：不展开，同上。输入是重做后的 drafts，本轮 11 条全部通过
                        route_after_verify(state) ⟶ "aggregate"
                            # 叶：不展开。没有失败意见，短路，不走重试环

                        aggregate(state) ⟶ {"report": {...},
                                            "history": ["aggregate：11 条意见，通过校验 11 条，待人工判断 0 条"]}
                            drafts = state.get("drafts") ⟶ {3 个文件}
                            verdicts = state.get("verdicts") ⟶ {3 个文件}
                            files = state.get("files") ⟶ {3 个文件}
                            settings = Settings.from_state(state.get("settings")) ⟶ Settings
                            file_reports = []; unresolved = []
                            by_severity = {"high": 0, "medium": 0, "low": 0}
                            total = 0; verified = 0
                            for path in sorted(drafts):                 # 三个文件逐个汇总
                                draft = drafts[path]; change = files.get(path)
                                by_id = {verdict.comment_id: verdict
                                         for verdict in verdicts.get(path, [])}
                                comments = []
                                for comment in draft.comments:
                                    verdict = by_id.get(comment.id)
                                    passed = bool(verdict and verdict.passed)
                                    total += 1
                                    by_severity[comment.severity] += 1
                                    if passed:
                                        verified += 1
                                    else:                           # 本轮没有，不执行
                                        issues = list(verdict.structural_issues) if verdict else ["没有校验结论"]
                                        if verdict and verdict.semantic_reason:
                                            issues.append(verdict.semantic_reason)
                                        unresolved.append(UnresolvedItem(...))
                                        # 到上限仍未通过的意见不丢弃，进报告的「未通过校验」一节
                                    comments.append(CommentReport(id=..., severity=..., title=...,
                                                                  detail=..., evidence=...,
                                                                  verified=passed))
                                sort(comments, key=(SEVERITY_ORDER[severity], line_start))
                                file_reports.append(FileReport(path=path, kind=draft.kind,
                                                               lang=change.lang, change_type=change.change_type,
                                                               comments=comments))
                            report = Report(model="deepseek-chat", source="patch samples\\demo.diff",
                                            stats=ReportStats(files_reviewed=3, comments_total=11,
                                                              comments_verified=11,
                                                              by_severity=by_severity, rounds=1),
                                            files=file_reports, unresolved=[])
                            return {"report": report.model_dump(), **log(...)}
                            W: report、history

                        human_gate(state) ⟶ 未返回：在此中断
                            payload = {"question": "报告已生成，选择下一步",
                                       "choices": ["approve", "revise", "abort"],
                                       "report": state.get("report") or {}}
                            answer = interrupt(payload) ⟶ 挂起
                                # 图在此原地暂停，状态写入检查点，节点未完成、不产生状态更新
                                # 恢复时本节点从头重跑，所以 interrupt 之前不能有副作用
                    # stream 结束。get_state 显示 next 停在 ("human_gate",)

                    snapshot = get_state(compiled, config) ⟶ StateSnapshot
                    interrupt_value = _first_interrupt(snapshot) ⟶ {"question": ..., "report": {...}}
                        for task in getattr(snapshot, "tasks", ()) or ():
                            for item in getattr(task, "interrupts", ()) or ():
                                return getattr(item, "value", None)
                        return None
                    if interrupt_value is None:                 # 不成立
                        return snapshot.values

                    report_payload = (interrupt_value or {}).get("report") or snapshot.values.get("report")
                    _print_report(report_payload)
                        if not report_payload:                  # 不成立
                            print("没有拿到报告内容")                                    [印]
                            return None
                        report = model_validate(Report, report_payload) ⟶ Report
                        print(Rule("审查报告"))                                          [印]
                        print(Markdown(report.to_markdown()))                            [印]
                        print(Rule())                                                    [印]

                    if pause_at_review:                         # False，不成立
                        print("已停在人工确认，进程直接退出，状态留在检查点里")                [印]
                        return snapshot.values

                    if reject_first and not rejected:           # 成立
                        rejected = True
                        decision = {"decision": "revise",
                                    "feedback": "少提一条：缺少对 records 为空时的处理，请补上"}
                        print(f"按 --reject-first 打回重做：{reject_first}")             [印]
                    elif auto_approve:
                        decision = {"decision": "approve"}
                        print("按 --auto-approve 通过")                                 [印]
                    else:
                        decision = _interactive_decision()

                    payload = Command(resume=decision)          # 带着人的选择回到 human_gate

                while True:                                    # ── 第 2 轮 ──
                    for chunk in stream(payload, config, stream_mode="updates"):
                        human_gate(state) ⟶ {"decision": "revise", "human_feedback": "少提一条：...",
                                             "revision_round": 0,
                                             "history": ["human_gate：选择 revise，反馈：少提一条：..."]}
                            # 恢复：本节点从头重跑，interrupt 直接返回人的选择
                            payload = {"question": "报告已生成，选择下一步",
                                       "choices": ["approve", "revise", "abort"],
                                       "report": state.get("report") or {}}
                            answer = interrupt(payload) ⟶ {"decision": "revise",
                                                           "feedback": "少提一条：缺少对 records 为空时的处理，请补上"}
                            if isinstance(answer, dict):                    # 成立
                                choice = "revise"
                                feedback = "少提一条：缺少对 records 为空时的处理，请补上"
                            else:
                                choice = str(answer).strip().lower()
                                feedback = ""
                            if choice not in CHOICES:                       # 不成立
                                choice, feedback = "abort", f"收到无法识别的选择 {choice!r}，按放弃处理"
                            return {"decision": "revise", "human_feedback": feedback,
                                    "revision_round": 0, **log(...)}
                            W: decision、human_feedback、revision_round（归零，打回后重新获得完整重做轮次）

                        route_after_human_gate(state) ⟶ "revise"
                            decision = state.get("decision", "abort") ⟶ "revise"
                            if decision == "approve":                       # 不成立
                                return "finalize"
                            if decision == "revise":                        # 成立
                                return "revise"
                            return END

                        revise(state) ⟶ {"drafts": {...}, "revision_round": 1, "human_feedback": "",
                                         "history": ["revise：第 1 轮，按人工反馈重做，涉及 3 个文件"]}
                            # 叶：不展开，同上。feedback 非空，走 if feedback 分支，
                            # 三个文件全部进 targets，整批重做

                        verify(state) ⟶ {"verdicts": {...},
                                         "history": ["verify：第 2 轮校验 14 条，未通过 2 条"]}
                            # 叶：不展开，同上
                        route_after_verify(state) ⟶ "revise"
                            # 叶：不展开。有 2 条没通过，且轮次 1 < 3，回到重做

                        revise(state) ⟶ {"drafts": {...}, "revision_round": 2, "human_feedback": "",
                                         "history": ["revise：第 2 轮，重做未通过的意见，涉及 2 个文件"]}
                            # 叶：不展开，同上。走 feedback 为空的分支
                        verify(state) ⟶ {"verdicts": {...},
                                         "history": ["verify：第 3 轮校验 12 条，未通过 0 条"]}
                            # 叶：不展开，同上
                        route_after_verify(state) ⟶ "aggregate"
                            # 叶：不展开。没有失败意见
                        aggregate(state) ⟶ {"report": {...},
                                            "history": ["aggregate：12 条意见，通过校验 12 条，待人工判断 0 条"]}
                            # 叶：不展开，同上
                        human_gate(state) ⟶ 未返回：再次中断
                            # 第二次暂停，流程与第 1 轮相同
                    snapshot = get_state(compiled, config) ⟶ StateSnapshot
                    interrupt_value = _first_interrupt(snapshot) ⟶ {...}
                    report_payload = ...
                    _print_report(report_payload)                                       [印]
                    if pause_at_review:                        # False
                        ...
                    if reject_first and not rejected:          # rejected 已是 True，不成立
                        ...
                    elif auto_approve:                         # 成立
                        decision = {"decision": "approve"}
                        print("按 --auto-approve 通过")                                 [印]
                    payload = Command(resume=decision)

                while True:                                    # ── 第 3 轮 ──
                    for chunk in stream(payload, config, stream_mode="updates"):
                        human_gate(state) ⟶ {"decision": "approve", "human_feedback": "",
                                             "revision_round": 0,
                                             "history": ["human_gate：选择 approve"]}
                            # 叶：不展开，同上，answer 为 {"decision": "approve"}
                        route_after_human_gate(state) ⟶ "finalize"
                            # 叶：不展开，decision 为 approve
                        finalize(state) ⟶ {"output_paths": ["out_reject\\report.json",
                                                           "out_reject\\report.md"],
                                           "history": ["finalize：写入 out_reject\\report.json 和 out_reject\\report.md"]}
                            report = model_validate(Report, state["report"] or {}) ⟶ Report
                            output_dir = Path(state.get("output_dir") or "out") ⟶ WindowsPath("out_reject")
                            mkdir(output_dir, parents=True, exist_ok=True) ⟶ None       [文]
                            json_path = output_dir / "report.json"
                            markdown_path = output_dir / "report.md"
                            write_text(json_path, report.model_dump_json(indent=2),
                                       encoding="utf-8")                               [文]
                            write_text(markdown_path, report.to_markdown(),
                                       encoding="utf-8")                               [文]
                            return {"output_paths": [str(json_path), str(markdown_path)], **log(...)}
                            W: output_paths、history
                    snapshot = get_state(compiled, config) ⟶ StateSnapshot
                    interrupt_value = _first_interrupt(snapshot) ⟶ None
                    if interrupt_value is None:                # 成立
                        return snapshot.values                 # 图跑完了

            decision = values.get("decision") ⟶ "approve"
            print(Rule("结果"))                                                        [印]
            if decision == "approve":                      # 成立
                for path in values.get("output_paths", []):
                    print(f"已输出 {path}")                                             [印]
                    # 依次打印 out_reject\report.json、out_reject\report.md
            else:
                print("已放弃，没有输出报告")
            return None
```

## 二 数据注入链

### 2.1 改动文本进入审查提示词

```
来源：patch 文件 samples/demo.diff                                          [文]
    │
    │ ingest 写入 state.diff_text
    ▼
state.diff_text（82 行）
    │
    │ parse_diff → parse_unified_diff
    ▼
FileChange.hunks（原始行，带 + - 空格前缀）
FileChange.added_lines = {新文件行号 → 行内容}      ← 行号校验的唯一依据
    │
    │ fan_out_files 按文件切成多路，每路只带走一个文件
    ▼
render_numbered(file_change, max_lines=400) ⟶ str
    │ 行号右对齐 + 符号 + 内容；超过 400 行截断并标注
    ▼
build_review_prompt(file_change, max_comments) ⟶ str
    │ │
    │ ├─ 注入位置 1：文件路径、改动类型、是否测试文件
    │ ├─ 注入位置 2：围栏内的带行号改动块（render_numbered 的结果）
    │ └─ 注入位置 3：review_profile(kind) —— 该类型的检查重点 + 通用检查方向
    ▼
structured_call(llm, CommentOut, REVIEW_SYSTEM, user)
    │ 以名为 CommentOut 的工具形式下发结构约束
    ▼
模型 → CommentOut.comments（file_path, line_start, line_end, severity,
                            category, title, detail, evidence）
```

### 2.2 意见进入校验提示词

```
CommentOut.comments
    │ ReviewComment.from_draft 逐条转换，补上 8 位 id
    ▼
FileDraft.comments ──写入──► state.drafts[文件路径]           # W，按文件路径覆盖
    │
    │ split_by_structural_check → check_comment
    ├──────► 位置校验不过：structural[comment.id] = 失败原因
    │            │
    │            ▼
    │        不经模型，直接进 verdicts，passed = False
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
structured_call(llm, SemanticCheckList, VERIFY_SYSTEM, user)
    ▼
SemanticCheckList.checks → {comment_id → SemanticCheck(supported, reason)}
    │
    ▼
CommentVerdict ──写入──► state.verdicts[文件路径]             # W，按文件路径覆盖
```

### 2.3 校验结论进入重做提示词

```
state.verdicts[文件路径] + state.drafts[文件路径] + state.human_feedback
    │
    │ revise 按文件聚合
    ├── 人工打回（feedback 非空）→ failures = ["人工审查反馈：..."],，该文件整批重做
    └── 自动重做 → failures = ["标题（行号 a-b）：失败原因", ...]
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
structured_call(llm, CommentOut, REVISE_SYSTEM, user)
    ▼
CommentOut.comments → 覆盖 state.drafts[文件路径]，回到 verify
```

### 2.4 配置与密钥进入每次模型调用

```
.env 文件 / 环境变量                                                    [文]
    │ load_settings → load_dotenv + getenv
    ▼
Settings（含 api_key）
    │
    │ Settings.for_state() 弹出 api_key
    ▼
state.settings（不含密钥）──► 存入检查点，落盘内容里不会出现密钥              [存][文]
    │
    │ 每个要模型的节点：Settings.from_state(state.settings)
    │ 发现 api_key 为空 → resolve_api_key() 从环境变量补回
    ▼
build_chat_model(settings) ⟶ ChatOpenAI(model, base_url, api_key, temperature, timeout)
    ▼
structured_call(...) → invoke(...)                                      [网]
```

### 2.5 结果进入报告文件

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
out_reject/report.json、out_reject/report.md                            [文]
```

### 2.6 人的选择进入图

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
