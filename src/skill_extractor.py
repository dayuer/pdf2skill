"""
Skill 提取器 — Phase 2: Constrained Extraction

根据文档类型自动选择提取策略：
- 技术手册/操作规范 → 提取操作步骤、排错流程、配置方法
- 叙事类 → 提取人物、事件、时间线、因果链、决策逻辑
- 方法论 → 提取思维模型、适用场景、核心步骤
- 学术教材 → 提取概念定义、前置知识、原理、示例

Prompt 从 prompts/ 目录版本化加载。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Optional, Callable

from .config import config
from .llm_client import AsyncDeepSeekClient, DeepSeekClient, LLMResponse
from .markdown_chunker import TextChunk
from .prompt_loader import load_prompt
from .schema_generator import SkillSchema
from .skill_validator import RawSkill, SkillValidator


# ──── 文档类型 → Prompt 映射 ────

# book_type → (prompt_name, user_prompt_template)
_TYPE_PROMPT_MAP: dict[str, tuple[str, str]] = {
    "技术手册": ("extractor", """以下是待提取的文本块：

> {context}

---

{content}

---

请从上述文本中提取所有可执行的操作规范，每个规范输出为一个独立的 Skill。
如果文本中包含多个独立的操作规范，输出多个 Skill（用 --- 分隔）。
如果文本中没有可提取的操作规范，输出 EMPTY_BLOCK。"""),

    "操作规范": ("extractor", """以下是待提取的文本块：

> {context}

---

{content}

---

请提取所有操作步骤和配置规范，每个规范输出为一个独立的 Skill（用 --- 分隔）。
无操作内容则输出 EMPTY_BLOCK。"""),

    "叙事类": ("narrative_extractor", """以下是待提取的文本块（来自叙事作品）：

> {context}

---

{content}

---

请从上述文本中提取所有关键「叙事知识单元」。每个单元输出为如下 YAML Frontmatter + Markdown Body 格式：

---
name: <事件的 kebab-case 英文名>
trigger: "<事件的一句话描述>"
domain: <所属主题领域>
characters:
  - "<角色1: 身份/立场>"
  - "<角色2: 身份/立场>"
timeline: "<在故事中的时间位置，如 第X章/第N个投资周期>"
source_ref: "<来源章节>"
confidence: <0.0-1.0>
prompt_version: "v0.1"
---

# 事件概要
<一段话概括发生了什么>

# 人物动机与决策
<关键角色的动机、目标、决策逻辑>

# 因果链
1. 起因：<为什么会发生>
2. 行动：<做了什么>
3. 结果：<导致了什么>
4. 后续影响：<对后续情节的影响>

# 核心逻辑
<IF/THEN 形式的决策逻辑>

# 主题标签
<该事件属于什么主题：商业决策/人物成长/阴谋博弈/伏笔回收等>

---

如果文本中包含多个独立事件，输出多个单元（用 --- 分隔）。
如果文本仅为过渡/日常描写，无关键事件或决策，输出 EMPTY_BLOCK。"""),

    "方法论": ("methodology_extractor", """以下是待提取的文本块：

> {context}

---

{content}

---

请提取可复用的思维模型或方法论框架，每个输出为一个独立的 Skill（用 --- 分隔）。
无方法论内容则输出 EMPTY_BLOCK。"""),

    "学术教材": ("academic_extractor", """以下是待提取的文本块：

> {context}

---

{content}

---

请提取知识图谱节点：概念定义、前置知识、核心原理、示例。
每个概念输出为一个独立的 Skill（用 --- 分隔）。
纯练习题或索引则输出 EMPTY_BLOCK。"""),
}

# 默认 fallback
_DEFAULT_PROMPT = ("extractor", _TYPE_PROMPT_MAP["技术手册"][1])


def _resolve_prompt_type(book_type: str) -> tuple[str, str]:
    """根据 book_type 解析出 (prompt_name, user_template)"""
    # 精确匹配
    if book_type in _TYPE_PROMPT_MAP:
        return _TYPE_PROMPT_MAP[book_type]

    # 模糊匹配
    bt = book_type.lower()
    if any(kw in bt for kw in ("叙事", "小说", "故事", "fiction", "narrative")):
        return _TYPE_PROMPT_MAP["叙事类"]
    if any(kw in bt for kw in ("方法", "框架", "methodology", "framework")):
        return _TYPE_PROMPT_MAP["方法论"]
    if any(kw in bt for kw in ("教材", "学术", "academic", "textbook")):
        return _TYPE_PROMPT_MAP["学术教材"]
    if any(kw in bt for kw in ("手册", "规范", "manual", "guide", "操作")):
        return _TYPE_PROMPT_MAP["技术手册"]

    return _DEFAULT_PROMPT


def _load_system_prompt(
    prompt_type: str, version: str, schema_constraint: str
) -> str:
    """加载系统提示词，优先从文件加载，降级为内联模板"""
    try:
        return load_prompt(prompt_type, version, schema_constraint=schema_constraint)
    except FileNotFoundError:
        return (
            "你是一个严苛的知识提取专家。"
            "你的任务是从文本中提取结构化知识单元。\n\n"
            f"{schema_constraint}"
        )


def _is_table_heavy(text: str) -> bool:
    """检测文本块中表格占比是否超过 50%"""
    lines = text.split("\n")
    table_lines = sum(1 for l in lines if "|" in l and l.strip().startswith("|"))
    return len(lines) > 0 and table_lines / len(lines) > 0.5


# ──── 同步提取 ────


def extract_skill_from_chunk(
    chunk: TextChunk,
    schema: SkillSchema,
    *,
    client: Optional[DeepSeekClient] = None,
    prompt_version: str = "v0.1",
    prompt_hint: str = "",
) -> list[RawSkill]:
    """从单个文本块中提取 Skill（同步）。"""
    if client is None:
        client = DeepSeekClient()

    constraint = schema.to_prompt_constraint()
    prompt_name, user_template = _resolve_prompt_type(schema.book_type)

    # 表格密集型覆盖
    if _is_table_heavy(chunk.content):
        prompt_name = "table_extractor"

    system = _load_system_prompt(prompt_name, prompt_version, constraint)

    # 用户调优指令注入
    if prompt_hint:
        system += f"\n\n## 用户调优指令\n\n{prompt_hint}"

    user = user_template.format(context=chunk.context, content=chunk.content)

    response = client.chat(system_prompt=system, user_prompt=user)

    client.logger.log(
        phase="extraction",
        prompt_version=prompt_version,
        system_prompt=system,
        user_prompt=user,
        response=response,
        input_metadata={
            "chunk_index": chunk.index,
            "heading_path": chunk.heading_path,
            "char_count": chunk.char_count,
            "book_type": schema.book_type,
            "prompt_hint": prompt_hint[:200] if prompt_hint else "",
        },
    )

    if "EMPTY_BLOCK" in response.content:
        return []

    return _parse_extraction_output(response.content, chunk)


def _parse_extraction_output(text: str, chunk: TextChunk) -> list[RawSkill]:
    """解析 R1 的提取输出为 RawSkill 列表。"""
    parts = [p.strip() for p in text.split("\n---\n") if p.strip()]
    if not parts:
        parts = [text.strip()]

    skills: list[RawSkill] = []
    for part in parts:
        if not part or "EMPTY_BLOCK" in part:
            continue
        skills.append(
            RawSkill(
                raw_text=part,
                source_chunk_index=chunk.index,
                source_context=chunk.context,
                source_heading_path=chunk.heading_path,
            )
        )

    return skills


# ──── 异步批量提取 ────


async def extract_skills_batch(
    chunks: list[TextChunk],
    schema: SkillSchema,
    *,
    client: Optional[AsyncDeepSeekClient] = None,
    prompt_version: str = "v0.1",
    prompt_hint: str = "",
    on_skill_extracted: Optional[Callable] = None,
) -> list[RawSkill]:
    """
    并行批量提取所有文本块中的 Skill。

    Args:
        chunks: 文本块列表
        schema: Skill Schema 模板
        client: 异步 DeepSeek 客户端
        prompt_version: Prompt 版本号
        prompt_hint: 用户调优指令，拼接到 system prompt 末尾
        on_skill_extracted: 回调函数，每提取到一个 Skill 时触发

    Returns:
        所有提取到的 RawSkill 列表
    """
    if client is None:
        client = AsyncDeepSeekClient()

    constraint = schema.to_prompt_constraint()
    prompt_name, user_template = _resolve_prompt_type(schema.book_type)

    tasks = []
    for chunk in chunks:
        pn = "table_extractor" if _is_table_heavy(chunk.content) else prompt_name
        system = _load_system_prompt(pn, prompt_version, constraint)

        # 用户调优指令注入
        if prompt_hint:
            system += f"\n\n## 用户调优指令\n\n{prompt_hint}"

        user = user_template.format(context=chunk.context, content=chunk.content)

        tasks.append({
            "system_prompt": system,
            "user_prompt": user,
            "metadata": {
                "chunk_index": chunk.index,
                "heading_path": chunk.heading_path,
                "char_count": chunk.char_count,
                "book_type": schema.book_type,
            },
        })

    responses = await client.batch_chat(
        tasks, phase="extraction", prompt_version=prompt_version
    )

    all_skills: list[RawSkill] = []
    for resp, chunk in zip(responses, chunks):
        if "EMPTY_BLOCK" in resp.content:
            continue
        skills = _parse_extraction_output(resp.content, chunk)
        all_skills.extend(skills)

        # 实时回调
        if on_skill_extracted and skills:
            for s in skills:
                on_skill_extracted(s)

    return all_skills
