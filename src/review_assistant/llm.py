"""模型调用的薄封装。

图上每个要模型的节点都通过这里拿客户端，这样换模型、换并发、加重试都只改一处。
"""

from __future__ import annotations

from typing import Any, TypeVar

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from .config import Settings

SchemaT = TypeVar("SchemaT", bound=BaseModel)


def build_chat_model(settings: Settings) -> ChatOpenAI:
    return ChatOpenAI(
        model=settings.model,
        base_url=settings.base_url,
        api_key=settings.api_key or "not-needed",
        temperature=settings.temperature,
        timeout=settings.timeout,
        max_retries=1,
    )


def structured_call(
    llm: ChatOpenAI,
    schema: type[SchemaT],
    system: str,
    user: str,
    attempts: int = 3,
) -> SchemaT:
    """要求模型按给定结构作答。

    对小模型或兼容接口，结构化输出偶尔会返回不合规的内容，所以这里重试几次；
    重试用完仍然失败就抛出去，由调用方决定怎么处理。
    """
    runnable = llm.with_structured_output(schema, method="function_calling")
    messages = [SystemMessage(content=system), HumanMessage(content=user)]
    last_error: Exception | None = None
    for _ in range(max(1, attempts)):
        try:
            result = runnable.invoke(messages)
        except Exception as exc:  # noqa: BLE001 - 向上抛出前需要重试
            last_error = exc
            continue
        if isinstance(result, schema):
            return result
        try:
            return schema.model_validate(result)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
    if last_error is None:
        raise RuntimeError("模型没有返回可解析的结构化结果")
    raise last_error


def ping(settings: Settings) -> str:
    """连通性自检：确认地址、密钥和模型名都能用。"""
    llm = build_chat_model(settings)
    reply = llm.invoke([HumanMessage(content="回复两个字：连通")])
    content: Any = reply.content
    if isinstance(content, list):
        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
    return str(content).strip()
