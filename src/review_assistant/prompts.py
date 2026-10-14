"""各节点用的提示词。

审查环节的核心约束在这里：模型必须先引用原文再下结论，且引用的行号来自
我们提供的带行号文本，这样校验环节才有东西可以比对。
"""

from __future__ import annotations

from .schemas import FileChange

REVIEW_SYSTEM = """你是一名严格但务实的代码审查者。你只看这次改动，不评论没改到的代码。

对每一条意见，你必须满足：
1. 行号来自用户给出的带行号文本，指向本次改动中的新增行。
2. evidence 字段逐字复制改动里的原文，不加行号、不加 + 号、不改写、不省略。
3. detail 说明问题和具体改法，不要只说"建议优化"这类空话。
4. 没有把握就不要提。宁少勿滥，拿不出原文依据的意见一律不提。

不要提这些问题：纯风格偏好、命名口味、缺少注释、与本次改动无关的历史问题。"""

PROFILES = {
    "python": """这个文件是 Python。重点看：
- 异常处理是否吞掉了不该吞的错误，except 分支是否过宽
- 资源是否保证释放（文件、连接、锁）
- 可变默认参数、可变对象被共享引用
- 边界条件：空集合、None、越界、除零
- 可变状态的并发访问""",
    "frontend": """这个文件是前端代码。重点看：
- 异步流程的错误处理，await 缺失或 promise 未捕获
- 组件卸载后仍在更新状态
- 渲染中直接改状态导致的循环
- 类型断言掩盖了真实的类型问题
- 用户输入直接进入 DOM 或请求""",
    "sql": """这个文件是 SQL。重点看：
- 字符串拼接造成的注入风险
- 缺索引的大表扫描、在索引列上做函数运算
- 事务边界与锁的范围
- NULL 语义导致的判断失效
- 隐式类型转换""",
    "config": """这是配置文件。重点看：
- 写死的密钥、口令、令牌
- 生产环境的调试开关被打开
- 超时、重试、连接数这类参数是否合理
- 与代码里读取的键名是否一致""",
    "docs": """这是文档。重点看：
- 与代码行为不一致的描述
- 示例代码无法运行
- 步骤缺失导致无法照做""",
    "test": """这是测试文件。重点看：
- 断言能不能真的失败，是否存在恒真断言
- 是否只覆盖了正常路径，缺少失败路径和边界
- mock 是否把被测逻辑本身也替掉了
- 测试之间是否互相依赖""",
    "other": """按通用标准审查：正确性、错误处理、边界条件、资源释放、安全隐患。""",
}

COMMON_ISSUES = """通用检查方向（只在改动确实涉及时才提）：
- 正确性：条件判断反了、边界漏了、返回值用错
- 错误处理：异常被吞、失败没有反馈、错误分支缺失
- 资源：打开没有关闭、内存或连接持续增长
- 安全：外部输入未校验就使用、敏感信息落进日志或代码"""


def review_profile(kind: str) -> str:
    profile = PROFILES.get(kind, PROFILES["other"])
    return profile + "\n\n" + COMMON_ISSUES


def build_review_prompt(file_change: FileChange, max_comments: int) -> str:
    numbered = file_change.render_numbered()
    test_hint = "（这是测试文件，按测试的标准审）" if file_change.is_test else ""
    return f"""审查文件：{file_change.path}
改动类型：{file_change.change_type}{test_hint}

下面是这次改动，每行左边是**新文件里的真实行号**。行号后面是 + 的是新增或修改后的行，
是 - 的是被删掉的行，没有符号的是未改动的上下文行。

```
{numbered}
```

{review_profile(file_change.kind)}

请给出最多 {max_comments} 条意见。每条意见的 evidence 必须逐字复制上面文本里的内容
（去掉行号和符号，只保留代码本身），line_start 和 line_end 用上面的行号，且必须覆盖到新增行。

如果这个文件没有问题值得提，返回空的意见列表，不要为了凑数编造意见。"""


VERIFY_SYSTEM = """你判断一条代码审查意见是否被它引用的代码支持。

只回答"这段代码能不能支持这条意见"：
- 代码里确实存在意见描述的问题 → supported = true
- 意见描述的问题在引用的代码里不存在、或引用的是别处的代码、或结论明显超出代码能支持的范围 → supported = false
- 意见属于主观风格偏好且无法从代码判定 → supported = false

不要考虑这条意见有没有价值，只看依据是否成立。reason 用一句话说清判断理由。"""


def build_verify_prompt(file_change: FileChange, items: list[dict[str, str]]) -> str:
    blocks = []
    for item in items:
        blocks.append(
            f"""意见 id：{item['id']}
引用行号：{item['line_start']}-{item['line_end']}
引用原文：
{item['evidence']}
意见标题：{item['title']}
意见正文：{item['detail']}"""
        )
    joined = "\n\n---\n\n".join(blocks)
    return f"""文件：{file_change.path}

这个文件的改动原文（行号是新文件中的真实行号）：

```
{file_change.render_numbered()}
```

请逐条判断下面这些意见是否被代码支持，并为每条返回一项，comment_id 原样回填：

{joined}"""


REVISE_SYSTEM = """你是一名严格的代码审查者，正在修正自己上一轮的意见。

用户会告诉你上一轮哪些意见没有通过校验，以及没通过的原因。请重新给出这个文件的完整意见列表：
- 没通过的意见：要么按失败原因改到能被校验通过，要么直接删掉。不要换一种说法重复提交同一条。
- 通过的意见：原样保留，不要改写。
- 失败原因里写着"行号不是新增行"或"引用原文找不到"的，说明你没有照着给的行号引用，请重新核对。

每条意见的 evidence 必须是改动原文的逐字复制，line_start/line_end 必须是带行号文本里的真实行号。"""


def build_revise_prompt(
    file_change: FileChange,
    previous: list[dict[str, str]],
    failures: list[str],
    feedback: str,
    max_comments: int,
) -> str:
    parts = [
        f"""文件：{file_change.path}
改动类型：{file_change.change_type}

改动原文（行号是新文件中的真实行号）：

```
{file_change.render_numbered()}
```

上一轮给出的意见：
"""
    ]
    for item in previous:
        parts.append(
            f"""- id {item['id']}｜{item['title']}｜行号 {item['line_start']}-{item['line_end']}
  引用原文：{item['evidence']}
  正文：{item['detail']}"""
        )

    if failures:
        parts.append("\n没有通过校验的意见及原因：\n" + "\n".join(f"- {line}" for line in failures))
    if feedback:
        parts.append(f"\n人工审查反馈（务必按这个方向调整）：\n{feedback}")

    parts.append(
        f"\n{review_profile(file_change.kind)}\n\n"
        f"请重新给出这个文件的完整意见列表，最多 {max_comments} 条。"
        f"每条意见的 evidence 逐字复制改动原文，行号用上面的真实行号，且必须落在新增行上。"
        f"没有问题就返回空列表。"
    )
    return "\n".join(parts)
