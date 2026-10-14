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

- 树形缩进表示函数的展开关系，控制结构保持接近源码的缩进。`⟶ result` 表示返回值绑定到 result。
- 展不展开按分析需要决定。不展开的函数作为叶节点，保留调用、结果摘要，并补足边界 R/W 与内部副作用。
- 展开的步骤单独成节，节内从被展开的那个函数起画树；上一节在调用处只留一行调用。
- `R:` 读取边界之外已有的数据，`W:` 修改边界之外已有的状态。
- 副作用：`[网]` 网络调用，`[印]` 输出，`[进]` 进程或命令，`[文]` 文件读写，`[存]` 持久化。副作用沿调用层级向上汇总。
- 同一个函数第二次出现时不再展开，作为叶节点并注明它走的是哪个分支。
- 按需要省略的部分：未展开函数的内部语句、未走到的分支体、框架内部（typer、langgraph 的调度实现、openai 客户端）。被省略的实现细节按数据流在第三节给出。
- 会话号、密钥等取自真实运行，密钥一律写成 `sk-...`。

## 一 路径总览

图上的连线：

```
                ┌── 扇出：每个文件一路 ── review_file ──┐
START → ingest → parse_diff → classify                ├→ verify ⇄ revise → aggregate → human_gate
                └── 没有可审文件时直接跳过 ────────────┘        └─ 打回回到 revise ─┘
                                                                    ├─ approve → finalize → END
                                                                    └─ abort   → END
```

`verify ⇄ revise` 是自动重做环：校验有没通过的、且轮次没到上限，就回 `revise` 重做，做完再回
`verify`。`human_gate` 的被人工打回也回到 `revise`，区别是整批重做并带上人的反馈。

这一次运行走过的步骤：

| 步 | 做什么 | 关键结果 |
| --- | --- | --- |
| 1 | `main()` → `app()` 解析命令行 → `run(...)` | 见 2.1 |
| 2 | 解析改动来源、路径过滤，读配置 | patch 文件；无路径过滤；模型 deepseek-chat |
| 3 | 建图并挂内存检查点 | 九个节点、两条环 |
| 4 | 组装初始状态 | 会话 2699f40ae94f；不含密钥 |
| 5 | 进入驱动循环 `_drive` | 见 2.2 |
| 6 | 第 1 轮：ingest → parse_diff → classify → 扇出 review_file 三路 → verify（12 条，1 条未通过）→ revise（只重做 1 个文件）→ verify（11 条，0 条未通过）→ aggregate（11 条）→ human_gate 暂停 | 自动重做环走完一轮 |
| 7 | 第 2 轮：打回 → revise（3 个文件整批重做）→ verify（14 条，2 条未通过）→ revise（2 个文件）→ verify（12 条，0 条未通过）→ aggregate（12 条）→ human_gate 再次暂停 | 人工打回环走完一轮，环内又走了一轮自动重做 |
| 8 | 第 3 轮：approve → finalize | 报告落盘 |
| 9 | 收尾：打印输出路径，进程退出 | out_reject/report.json、out_reject/report.md |

## 二 调用链

### 2.1 启动到进入驱动循环

```
main()                                                              # 进程入口  [网][印][文]
    app()                                                           # typer 解析参数并分发到 run  [印]
        run(from_git=None, patch="samples/demo.diff", diff_text=None,
            out="out_reject", kind="memory", db="checkpoints.db", thread=None, paths=None,
            model=None, base_url=None, max_concurrency=4, max_rounds=3,
            auto_approve=True, pause_at_review=False,
            reject_first="少提一条：缺少对 records 为空时的处理，请补上")
            source = _resolve_source(None, "samples/demo.diff", None) ⟶ {"type": "patch", "path": "samples/demo.diff"}
                # 叶：三个来源互斥校验后按 --patch 返回；另两个分支本次未走
            path_filter = parse_paths(None) ⟶ []
                # 叶：raw 为空，直接返回空列表
            settings = load_settings(model=None, base_url=None, max_concurrency=4, max_rounds=3) ⟶ Settings
                # 叶：load_dotenv 读 .env 再取环境变量；命令行给的 4 和 3 覆盖同名环境变量  [文]
                # api_key 由 resolve_api_key() 取，命中 DEEPSEEK_API_KEY ⟶ "sk-..."
                # R: 环境变量、.env 文件
            thread_id = None or uuid4().hex[:12] ⟶ "2699f40ae94f"
            config = {"configurable": {"thread_id": "2699f40ae94f"}, "max_concurrency": 4}

            print("会话 2699f40ae94f｜模型 deepseek-chat｜检查点 memory｜并发 4")                 [印]
            if path_filter:                                        # [] 为假，不成立
                print("只审查路径里包含 [] 的文件")                                               [印]

            compiled = build_graph(make_checkpointer("memory", "checkpoints.db")) ⟶ CompiledStateGraph
                make_checkpointer("memory", "checkpoints.db") ⟶ InMemorySaver
                    # 叶：把六个自定义类型登记进序列化器白名单，检查点先内存后 SQLite
                build_graph(saver=None) ⟶ CompiledStateGraph
                    # 叶：登记九个节点并按第一节的连线 compile；检查点是 InMemorySaver

            initial = {"diff_source": {"type": "patch", "path": "samples/demo.diff"},
                       "output_dir": "out_reject",
                       "path_filter": [],
                       "settings": settings.for_state() ⟶ {...不含 api_key...}
                           # 叶：model_dump 后弹掉 api_key，因为检查点会落盘
                       "drafts": {}, "verdicts": {},
                       "revision_round": 0, "human_feedback": "", "history": []}
            W: 这是整张图的初始状态，之后每个节点都在它之上累积

            if pause_at_review:                                    # False，跳过
                print(f"恢复命令：review resume --thread {thread_id} --db {db} --auto-approve")   [印]

            values = _drive(compiled, initial, config, True, "少提一条：...", False) ⟶ dict
                # 见 2.2

            decision = values.get("decision") ⟶ "approve"
            print(Rule("结果"))                                                                  [印]
            if decision == "approve":                              # 成立
                for path in values.get("output_paths", []):
                    print(f"已输出 {path}")                                                       [印]
                    # 依次是 out_reject\report.json、out_reject\report.md
            else:
                print("已放弃，没有输出报告")                                                     [印]
            return None
```

### 2.2 驱动循环与三轮执行

```
_drive(compiled, initial, config, True, "少提一条：缺少对 records 为空时的处理，请补上", False) ⟶ dict
    # 由 2.1 的 run 调用。三个 while 轮次对应三次 stream：初始状态、第一次恢复、第二次恢复
    rejected = False
    while True:                                                    # ── 第 1 轮 ──
        for chunk in stream(initial, config, stream_mode="updates"):
            # stream 由 LangGraph 驱动整张图：按连线调用节点，每完成一个产出一个 chunk。
            # 节点之间不直接互调，数据都经共享状态传递。下面按发生顺序列本轮实际走过的节点。
            ingest(state) ⟶ {"diff_text": <82 行>, "source": "patch samples\\demo.diff"}
                # 叶：读 patch 文件正文   [文]
            parse_diff(state) ⟶ {"files": <3 个文件>}
                # 叶：parse_unified_diff 拆出 3 个文件、35 行新增，新增行记成 {行号 → 内容}
            classify(state) ⟶ {"files": <3 个文件，kind 已填>}
                # 叶：path_filter 为空不做过滤；按路径给每个文件打上 kind / lang
            fan_out_files(state) ⟶ [Send("review_file", {...}) × 3]
                # 见 2.3
            review_file({"file": <app/service.py>, "settings": {...}}) ⟶ {"drafts": {...}}
                # 见 2.4
            review_file({"file": <tests/test_service.py>, "settings": {...}}) ⟶ {"drafts": {...}}
            review_file({"file": <config/prod.yaml>, "settings": {...}}) ⟶ {"drafts": {...}}
                # 叶：三路由框架并发调度，各自只写 drafts 里自己那个键；本次分别生成 2 条、3 条意见
            verify(state) ⟶ {"verdicts": {...}}
                # 叶：第 1 轮校验 12 条，1 条未通过。先做位置校验（文件名、行号落在新增行、
                # 引用原文逐字存在），位置过关的才送去调模型判语义
            route_after_verify(state) ⟶ "revise"
                # 叶：有未通过的，且 revision_round 0 < max_rounds 3
            revise(state) ⟶ {"drafts": {...}, "revision_round": 1}
                # 叶：只重做有失败意见的那个文件，失败原因一并交给模型
            verify(state) ⟶ {"verdicts": {...}}
                # 叶：第 2 轮校验 11 条，0 条未通过
            route_after_verify(state) ⟶ "aggregate"
                # 叶：没有失败意见，不再进环
            aggregate(state) ⟶ {"report": {...}}
                # 叶：按 id 把 drafts 与 verdicts 对齐，汇总成报告；11 条全部通过
            human_gate(state) ⟶ 未返回：在此中断
                # 见 2.5

        # stream 结束，图停在 human_gate：该节点未完成，不产生状态更新
        snapshot = get_state(compiled, config) ⟶ StateSnapshot
        interrupt_value = _first_interrupt(snapshot) ⟶ {"question": "报告已生成，选择下一步", "report": {...}}
            # 叶：从 snapshot.tasks[*].interrupts[*].value 取回暂停时抛出的人机问答内容
        if interrupt_value is None:                                # 不成立
            return snapshot.values

        report_payload = (interrupt_value or {}).get("report") or snapshot.values.get("report")
        _print_report(report_payload)
            # 叶：把报告按 Markdown 打到终端   [印]

        if pause_at_review:                                        # False，跳过
            print("已停在人工确认，进程直接退出，状态留在检查点里")                              [印]
            return snapshot.values

        if reject_first and not rejected:                          # 成立
            rejected = True
            decision = {"decision": "revise", "feedback": "少提一条：缺少对 records 为空时的处理，请补上"}
            print("按 --reject-first 打回重做：少提一条：...")                                 [印]
        elif auto_approve:
            decision = {"decision": "approve"}
            print("按 --auto-approve 通过")                                                    [印]
        else:
            decision = _interactive_decision()
                # 叶：从 stdin 读选择
        payload = Command(resume=decision)                         # 带着人的选择回到 human_gate

    while True:                                                    # ── 第 2 轮 ──
        for chunk in stream(payload, config, stream_mode="updates"):
            # 循环体与第 1 轮相同，不再展开；本轮实际走过的节点如下
            human_gate(state) ⟶ {"decision": "revise", "human_feedback": "少提一条：...", "revision_round": 0}
                # 见 2.5 的恢复一段
            route_after_human_gate(state) ⟶ "revise"
                # 叶：decision 为 revise
            revise(state) ⟶ {"drafts": {...}, "revision_round": 1}
                # 叶：human_feedback 非空，3 个文件整批重做
            verify(state) ⟶ {"verdicts": {...}}
                # 叶：第 2 轮校验 14 条，2 条未通过
            route_after_verify(state) ⟶ "revise"
                # 叶：有未通过的，且轮次没到上限
            revise(state) ⟶ {"drafts": {...}, "revision_round": 2}
                # 叶：走 feedback 为空的分支，重做 2 个文件里未通过的意见
            verify(state) ⟶ {"verdicts": {...}}
                # 叶：第 3 轮校验 12 条，0 条未通过
            route_after_verify(state) ⟶ "aggregate"
            aggregate(state) ⟶ {"report": {...}}
                # 叶：12 条意见，12 条通过
            human_gate(state) ⟶ 未返回：再次中断

        snapshot = get_state(compiled, config) ⟶ StateSnapshot
        interrupt_value = _first_interrupt(snapshot) ⟶ {"question": ..., "report": {...}}
        report_payload = (interrupt_value or {}).get("report") or snapshot.values.get("report")
        _print_report(report_payload)                                                          [印]
        if reject_first and not rejected:                          # rejected 已是 True，不成立
            ...
        elif auto_approve:                                         # 成立
            decision = {"decision": "approve"}
        payload = Command(resume=decision)

    while True:                                                    # ── 第 3 轮 ──
        for chunk in stream(payload, config, stream_mode="updates"):
            human_gate(state) ⟶ {"decision": "approve", "human_feedback": "", "revision_round": 0}
                # 叶：answer 为 {"decision": "approve"}
            route_after_human_gate(state) ⟶ "finalize"
                # 叶：decision 为 approve
            finalize(state) ⟶ {"output_paths": ["out_reject\\report.json", "out_reject\\report.md"]}
                # 叶：建目录，写 report.json 与 report.md   [文]
        snapshot = get_state(compiled, config) ⟶ StateSnapshot
        interrupt_value = _first_interrupt(snapshot) ⟶ None
        if interrupt_value is None:                                # 成立
            return snapshot.values                                 # 图跑完，返回最终状态

    W: 图的共享状态，由各节点经 stream 写入
```

### 2.3 fan_out_files

```
fan_out_files(state) ⟶ [Send("review_file", {"file": <app/service.py>, "settings": {...}}),
                        Send("review_file", {"file": <tests/test_service.py>, ...}),
                        Send("review_file", {"file": <config/prod.yaml>, ...})]
    # 由 2.2 第 1 轮 classify 之后调用。返回一批 Send 表示"分派"，而不是下一个节点名
    files = state.get("files") ⟶ {3 个文件}
    if not files:                                              # 不成立
        return "aggregate"                                     # 没有可审文件时退回主线
    payload_settings = state.get("settings") ⟶ {...不含 api_key...}
    return [Send("review_file", {"file": change.model_dump(), "settings": payload_settings})
            for change in files.values()
            if not change.is_binary] or "aggregate"
    # 三个文件都不是二进制，得到三条分派指令。每路只带走一个文件和 settings，所以
    # review_file 里看不到共享状态的其他字段
    R: state.files、state.settings
```

### 2.4 review_file（三路中的一路）

```
review_file({"file": <app/service.py>, "settings": {...}}) ⟶ {"drafts": {"app/service.py": FileDraft}}
    # 由 2.3 分派的三路之一。另两路代码完全相同，只是文件不同
    file_change = model_validate(FileChange, state["file"]) ⟶ FileChange
    settings = Settings.from_state(state.get("settings")) ⟶ Settings
        # 叶：state 里没有 api_key，这里从环境变量补回来   R: 环境变量
    llm = build_chat_model(settings) ⟶ ChatOpenAI
        # 叶：只构造客户端，不发起请求
    draft = generate_comments(llm, file_change, settings, attempt=0) ⟶ FileDraft
        # 叶：拼出审查提示词（带行号改动块 + 该类型的检查重点 + 条数上限），要求模型按
        # CommentOut 作答，每条意见在此拿到一个 8 位 id   [网]
    return {"drafts": {"app/service.py": draft}, **log("review_file：app/service.py 生成 7 条意见")}
    W: drafts["app/service.py"]、history
```

### 2.5 human_gate（首次暂停与恢复）

```
human_gate(state) ⟶ 未返回：在此中断
    # 由 2.2 第 1 轮 aggregate 之后调用。全程只有这个节点会暂停
    payload = {"question": "报告已生成，选择下一步",
               "choices": ["approve", "revise", "abort"],
               "report": state.get("report") or {}}
    answer = interrupt(payload) ⟶ 挂起
        # 第一次调用到这里就停住：状态写进检查点，节点不算完成、不产生状态更新，进程可以退出。
        # 恢复时本节点从头重跑，所以 interrupt 之前不能有副作用
    if isinstance(answer, dict):                               # 恢复后：成立
        choice = str(answer.get("decision", "")).strip().lower() ⟶ "revise"
        feedback = str(answer.get("feedback", "")).strip() ⟶ "少提一条：缺少对 records 为空时的处理，请补上"
    else:
        choice = str(answer).strip().lower()
        feedback = ""
    if choice not in CHOICES:                                  # 不成立
        choice, feedback = "abort", f"收到无法识别的选择 {choice!r}，按放弃处理"
    return {"decision": choice, "human_feedback": feedback, "revision_round": 0,
            **log(f"human_gate：选择 {choice}" + (f"，反馈：{feedback}" if feedback else ""))}
    W: decision、human_feedback、revision_round（归零，打回后重新获得完整的重做轮次）、history
```

第 2 轮那次暂停走的是同一段代码，恢复时 `answer` 为 `{"decision": "approve"}`，于是
`decision` 写 `approve`、反馈为空，图转到 `finalize`。

## 三 数据注入链

### 3.1 改动文本进入审查提示词

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

### 3.2 意见进入校验提示词

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

### 3.3 校验结论进入重做提示词

```
state.verdicts[文件路径] + state.drafts[文件路径] + state.human_feedback
    │
    │ revise 按文件聚合
    ├── 人工打回（feedback 非空）→ failures = ["人工审查反馈：..."], 该文件整批重做
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

### 3.4 配置与密钥进入每次模型调用

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

### 3.5 结果进入报告文件

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

### 3.6 人的选择进入图

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
