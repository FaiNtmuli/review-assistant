# 代码审查助手

输入一份代码改动，输出一份每条意见都带行号依据的审查报告。

```
读入改动 → 解析出每个文件的新增行 → 按文件类型分类
   → 每个文件并发生成审查意见
   → 逐条校验意见有没有具体依据（文件、行号、引用原文）
   → 没依据的只重做那几条，最多三轮
   → 汇总成结构化报告
   → 人工确认（通过 / 打回重做 / 放弃）
   → 落盘 report.json 和 report.md
```

技术栈：Python 3.11+、LangGraph、LangChain。模型走任意 OpenAI 兼容接口，
DeepSeek 和本地服务（Ollama、vLLM、LM Studio）都只改两个配置项。

## 安装

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

## 配置

把 `.env.example` 复制成 `.env` 填入密钥。接本地服务时改这两项：

```
REVIEW_BASE_URL=http://127.0.0.1:11434/v1
REVIEW_MODEL=qwen2.5-coder:14b
REVIEW_API_KEY=ollama
```

其他可选配置：`REVIEW_MAX_CONCURRENCY`（并发模型调用数）、`REVIEW_MAX_ROUNDS`（重做上限）。

## 用法

```powershell
# 连通性自检：确认地址、密钥、模型名，以及结构化输出能不能用
review doctor

# 打印图的节点和连线
review graph

# 只看读入和解析结果，不调模型
review ingest --patch samples/demo.diff --numbered

# 跑完整流程
review run --patch samples/demo.diff --out out --auto-approve
review run --from-git HEAD~1 --out out
```

人工确认有两个分支可以试：

```powershell
# 第一次人工确认直接打回并带上反馈，之后自动通过
review run --patch samples/demo.diff --out out --reject-first "缺少对空输入的判断" --auto-approve

# 换 SQLite 检查点，停在人工确认后进程退出，再用另一个进程接上
review run --patch samples/demo.diff --checkpointer sqlite --db demo.db --thread demo1 --pause-at-review
review resume --thread demo1 --db demo.db --auto-approve
```

## 图上的九个节点

| 节点 | 做什么 | 调模型 |
| --- | --- | --- |
| `ingest` | 把改动读成一个字符串，来源可以是 git 范围、patch 文件或直接给文本 | 否 |
| `parse_diff` | 拆成每个文件一段，记下每个文件的真实新增行号 | 否 |
| `classify` | 按路径判定文件类型，决定用哪套检查重点 | 否 |
| `review_file` | 对单个文件生成意见，每个文件一路并发 | 是 |
| `verify` | 先查文件名、行号、引用原文，再判断意见是否被代码支持 | 是 |
| `revise` | 只重做没通过的意见；人工打回时按反馈整批重做 | 是 |
| `aggregate` | 汇总成结构化报告 | 否 |
| `human_gate` | 暂停等人选择：通过 / 打回 / 放弃 | 否 |
| `finalize` | 落盘 report.json 和 report.md | 否 |

连线：主链顺序执行；`classify` 用 `Send` 按文件扇出，全部跑完汇合到 `verify`；
`verify` 和 `revise` 之间有一条重试环；`human_gate` 打回时回到 `revise`。

## 校验是怎么判定的

`verify` 分两层，第一层不联网：

1. 文件名在本次改动里；
2. `line_start` 到 `line_end` 里至少有一行是本次新增的行；
3. 给出的 `evidence` 逐字出自这次改动；
4. `evidence` 落在所引用的行号区间上。

四条都过才交给模型判断语义是否成立。跑不过第一层的意见不会浪费模型调用，
而是带着失败原因回到 `revise` 重写。

## 目录

```
src/review_assistant/
  schemas.py        数据结构
  state.py          图的状态和合并规则
  diff_parser.py    unified diff 解析
  classifier.py     文件类型判定
  verification.py   意见依据的纯代码校验
  prompts.py        提示词
  llm.py            模型调用封装
  graph.py          图组装
  cli.py            命令行
  nodes/            九个节点
tests/              单元测试，不联网
samples/demo.diff   演示用的改动
```

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest
```

测试全部离线，覆盖 diff 解析、文件分类、意见校验规则和图的路由分支。

更详细的设计取舍见 [docs/design.md](docs/design.md)。
