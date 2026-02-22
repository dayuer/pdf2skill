"""
Schema 生成器 — Phase 0: Schema Genesis

从 TOC + 前言自动生成该书专属的 Skill Schema 模板。
使用 DeepSeek R1 推断书籍应包含的核心组件、知识领域和 Skill 字段结构。
支持人工 Override。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .llm_client import DeepSeekClient, LLMResponse
from .markdown_chunker import _extract_headings


@dataclass
class SkillSchema:
    """Skill Schema 模板"""

    # 书籍信息
    book_name: str
    # 推断出的领域列表
    domains: list[str] = field(default_factory=list)
    # Skill 字段定义
    fields: dict = field(default_factory=dict)
    # R1 推断的书籍类型
    book_type: str = ""
    # 原始 R1 输出
    raw_output: str = ""

    def to_prompt_constraint(self) -> str:
        """将 Schema 转为 Prompt 中的输出约束"""
        domain_list = ", ".join(self.domains) if self.domains else "general"
        return f"""你必须严格按以下 YAML Frontmatter 格式输出每个 Skill：

```yaml
name: <kebab-case 英文名称，全局唯一>
trigger: <中文描述：什么场景下调用此 Skill>
domain: <从以下领域中选择：{domain_list}>
prerequisites:
  - <前置依赖，如"需要获取XX数据">
source_ref: <原文出处：章节名称>
confidence: <0.0-1.0 的置信度自评>
```

Frontmatter 之后是 Markdown 正文，包含：
- 「# 执行步骤」：用 1, 2, 3 编号，包含 IF/ELSE 判断分支
- 「# 输出格式要求」：规定 AI 最终回答用户的格式"""

    def save(self, filepath: str | Path) -> None:
        """保存 Schema 到 JSON 文件"""
        data = {
            "book_name": self.book_name,
            "domains": self.domains,
            "fields": self.fields,
            "book_type": self.book_type,
        }
        Path(filepath).write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    @classmethod
    def load(cls, filepath: str | Path) -> SkillSchema:
        """从 JSON 文件加载 Schema"""
        data = json.loads(Path(filepath).read_text(encoding="utf-8"))
        return cls(**data)


# ──── Schema Genesis Prompt ────

_SCHEMA_SYSTEM_PROMPT = """你是一个严苛的知识架构师。你的任务是分析一本书的目录和前言，推断该书的知识结构。

你必须输出一个 JSON 对象，包含以下字段：
{
  "book_type": "技术手册 | 方法论 | 操作规范 | 学术教材 | 叙事类",
  "domains": ["领域1", "领域2", ...],
  "core_components": ["该书应该覆盖的核心组件/概念"],
  "skill_types": ["该书能提取的 Skill 类型，如：故障排查、配置部署、分析框架"]
}

规则：
1. domains 使用 kebab-case 英文命名
2. 仅基于目录和前言内容推断，禁止编造
3. 直接输出 JSON，禁止输出任何解释性文字"""


def _extract_toc_and_preface(text: str, max_chars: int = 6000) -> str:
    """
    从 Markdown 文本中提取目录结构和前言部分。

    策略：
    1. 提取所有标题行作为 TOC
    2. 取前 max_chars 字符作为前言
    """
    headings = _extract_headings(text)

    # 构建 TOC
    toc_lines = []
    for h in headings:
        indent = "  " * (h.level - 1)
        toc_lines.append(f"{indent}- {h.title}")

    toc_str = "## 目录结构\n\n" + "\n".join(toc_lines)

    # 取前言（前 max_chars 字符）
    preface = text[:max_chars]
    if len(text) > max_chars:
        preface += "\n\n[... 正文省略 ...]"

    return f"{toc_str}\n\n## 前言摘要\n\n{preface}"


def generate_schema(
    text: str,
    book_name: str,
    *,
    client: Optional[DeepSeekClient] = None,
) -> SkillSchema:
    """
    Phase 0：从全文中提取 TOC + 前言，生成 Skill Schema。

    Args:
        text: 完整 Markdown 文本
        book_name: 书名
        client: DeepSeek 客户端（默认创建新实例）

    Returns:
        SkillSchema 模板
    """
    if client is None:
        client = DeepSeekClient()

    # 提取 TOC + 前言
    toc_preface = _extract_toc_and_preface(text)

    # 调用 R1
    response = client.chat(
        system_prompt=_SCHEMA_SYSTEM_PROMPT,
        user_prompt=f"以下是《{book_name}》的目录和前言：\n\n{toc_preface}",
    )

    # 从输出中提取 JSON
    schema_data = _parse_schema_json(response.content)

    # 记录日志
    client.logger.log(
        phase="schema_genesis",
        prompt_version="v0.1",
        system_prompt=_SCHEMA_SYSTEM_PROMPT,
        user_prompt=toc_preface[:500],
        response=response,
        input_metadata={"book_name": book_name},
    )

    return SkillSchema(
        book_name=book_name,
        domains=schema_data.get("domains", ["general"]),
        fields=schema_data,
        book_type=schema_data.get("book_type", "unknown"),
        raw_output=response.content,
    )


def _parse_schema_json(text: str) -> dict:
    """从 R1 输出中提取 JSON 对象（容错处理）"""
    # 尝试直接解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 尝试从 markdown 代码块中提取
    json_match = re.search(r"```(?:json)?\s*\n(.*?)\n```", text, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group(1))
        except json.JSONDecodeError:
            pass

    # 尝试提取第一个 { ... } 块
    brace_match = re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", text, re.DOTALL)
    if brace_match:
        try:
            return json.loads(brace_match.group(0))
        except json.JSONDecodeError:
            pass

    # 全部失败，返回默认值
    return {"book_type": "unknown", "domains": ["general"], "core_components": [], "skill_types": []}
