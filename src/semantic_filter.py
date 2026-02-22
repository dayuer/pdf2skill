"""
语义密度粗筛器 — Phase 1C: Semantic Filtering

双通道评估文本块的信息价值，过滤低密度内容。

通道 A（独立价值）：是否包含操作步骤、方法论、规则、参数
通道 B（上下文锚点）：是否定义作用域、前提条件、术语定义

丢弃规则：仅当通道 A ≤ 2 且通道 B 无锚点时丢弃。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Optional

from .config import config
from .llm_client import AsyncDeepSeekClient, DeepSeekClient, LLMResponse
from .markdown_chunker import TextChunk


@dataclass
class FilterResult:
    """单个文本块的筛选结果"""

    chunk: TextChunk
    # 通道 A 评分：1-5（操作性内容密度）
    score_a: int
    # 通道 B：是否包含上下文锚点
    has_anchor: bool
    # 是否保留
    keep: bool
    # 筛选理由
    reason: str = ""


@dataclass
class FilterBatchResult:
    """批量筛选结果"""

    kept: list[TextChunk]
    dropped: list[FilterResult]
    total: int
    kept_count: int
    dropped_count: int


# ──── 评分规则（本地规则优先，减少 LLM 调用） ────

# 高信息密度关键词（通道 A）
_HIGH_VALUE_PATTERNS = [
    # 操作指令
    "步骤", "执行", "运行", "配置", "安装", "部署", "启动", "停止",
    "命令", "脚本", "参数", "设置", "创建", "删除", "修改", "更新",
    # 条件判断
    "如果", "若", "当", "则", "否则", "大于", "小于", "等于",
    "条件", "判断", "检查", "验证", "确认",
    # 方法论
    "规则", "原则", "策略", "方法", "流程", "标准", "规范",
    "公式", "算法", "模型", "框架",
    # 因果关系
    "因为", "所以", "导致", "引起", "原因", "结果", "影响",
    # 代码信号
    "```", "`", "def ", "class ", "import ", "SELECT ", "CREATE ",
    "kubectl", "docker", "git ", "npm ", "pip ",
]

# 低信息密度关键词（通道 A 减分）
_LOW_VALUE_PATTERNS = [
    "简介", "概述", "背景", "历史", "发展", "演变",
    "致谢", "感谢", "参考文献", "附录",
    "前言", "序言", "导言",
]

# 上下文锚点关键词（通道 B）
_ANCHOR_PATTERNS = [
    # 作用域限定
    "仅适用于", "限于", "以下场景", "在此条件下",
    "适用范围", "前提条件", "前提假设",
    # 术语定义
    "定义为", "指的是", "是指", "称为", "简称",
    "术语", "概念", "含义",
    # 约束声明
    "必须", "禁止", "不得", "要求", "强制",
    "注意", "警告", "重要",
]


def _score_value(text: str) -> int:
    """
    通道 A：评估文本的独立操作价值（1-5 分）。

    纯本地规则，不消耗 LLM Token。
    """
    text_lower = text.lower()
    high_hits = sum(1 for p in _HIGH_VALUE_PATTERNS if p in text_lower)
    low_hits = sum(1 for p in _LOW_VALUE_PATTERNS if p in text_lower)

    # 代码块数量加分
    code_blocks = text.count("```")

    # 编号列表加分（操作步骤信号）
    import re
    numbered_steps = len(re.findall(r"^\s*\d+\.\s", text, re.MULTILINE))

    raw_score = high_hits * 2 + code_blocks * 3 + numbered_steps * 2 - low_hits * 3

    # 归一化到 1-5
    if raw_score >= 15:
        return 5
    elif raw_score >= 10:
        return 4
    elif raw_score >= 5:
        return 3
    elif raw_score >= 1:
        return 2
    else:
        return 1


def _has_anchor(text: str) -> bool:
    """通道 B：检测是否包含上下文锚点。"""
    text_lower = text.lower()
    return any(p in text_lower for p in _ANCHOR_PATTERNS)


def filter_chunk(chunk: TextChunk) -> FilterResult:
    """
    评估单个文本块。

    丢弃规则：通道 A ≤ 2 且通道 B 无锚点。
    """
    score_a = _score_value(chunk.content)
    has_anc = _has_anchor(chunk.content)

    keep = True
    reason = ""

    if score_a <= 2 and not has_anc:
        keep = False
        reason = f"低信息密度（评分 {score_a}/5）且无上下文锚点"
    elif score_a <= 2 and has_anc:
        reason = f"保留为上下文锚点（评分 {score_a}/5 但含作用域/定义）"
    else:
        reason = f"高价值内容（评分 {score_a}/5）"

    return FilterResult(
        chunk=chunk,
        score_a=score_a,
        has_anchor=has_anc,
        keep=keep,
        reason=reason,
    )


def filter_chunks(chunks: list[TextChunk]) -> FilterBatchResult:
    """
    批量筛选文本块。

    纯本地规则评估，不消耗任何 LLM Token。

    Args:
        chunks: 文本块列表

    Returns:
        FilterBatchResult 包含保留/丢弃的块和统计
    """
    kept: list[TextChunk] = []
    dropped: list[FilterResult] = []

    for chunk in chunks:
        result = filter_chunk(chunk)
        if result.keep:
            kept.append(chunk)
        else:
            dropped.append(result)

    return FilterBatchResult(
        kept=kept,
        dropped=dropped,
        total=len(chunks),
        kept_count=len(kept),
        dropped_count=len(dropped),
    )


# ──── LLM 增强评估（可选，用于高价值场景） ────

_FILTER_SYSTEM_PROMPT = """你是一个信息密度评估专家。评估以下文本的操作价值。

输出格式（仅输出 JSON，禁止其他内容）：
{
  "score": 1-5,
  "has_anchor": true/false,
  "reason": "一句话理由"
}

评分标准：
5 = 包含完整的操作步骤/配置方法/排错流程
4 = 包含可操作的规则或方法论框架
3 = 部分可操作，含有用的参数或条件
2 = 主要是背景描述，少量可操作内容
1 = 纯背景/叙述/致谢/引言，无操作价值"""


async def filter_chunks_with_llm(
    chunks: list[TextChunk],
    *,
    client: Optional[AsyncDeepSeekClient] = None,
    threshold: int = 2,
) -> FilterBatchResult:
    """
    使用 LLM 进行语义密度评估（高精度，有 Token 成本）。

    建议仅在本地规则筛选后的「边界案例」上使用。
    """
    if client is None:
        client = AsyncDeepSeekClient()

    import json

    kept: list[TextChunk] = []
    dropped: list[FilterResult] = []

    tasks = [
        {
            "system_prompt": _FILTER_SYSTEM_PROMPT,
            "user_prompt": chunk.content[:2000],  # 截断控制成本
            "metadata": {"chunk_index": chunk.index},
        }
        for chunk in chunks
    ]

    responses = await client.batch_chat(
        tasks, phase="semantic_filter", prompt_version="v0.1"
    )

    for resp, chunk in zip(responses, chunks):
        try:
            data = json.loads(resp.content)
            score = int(data.get("score", 3))
            has_anc = bool(data.get("has_anchor", False))
        except (json.JSONDecodeError, ValueError):
            # 解析失败，保守保留
            score = 3
            has_anc = False

        keep = score > threshold or has_anc
        result = FilterResult(
            chunk=chunk,
            score_a=score,
            has_anchor=has_anc,
            keep=keep,
            reason=data.get("reason", "") if 'data' in dir() else "",
        )

        if keep:
            kept.append(chunk)
        else:
            dropped.append(result)

    return FilterBatchResult(
        kept=kept,
        dropped=dropped,
        total=len(chunks),
        kept_count=len(kept),
        dropped_count=len(dropped),
    )
