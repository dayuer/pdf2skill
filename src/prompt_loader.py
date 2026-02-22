"""
Prompt 加载器 — 版本化管理 Prompt 模板

从 prompts/ 目录加载指定版本的 Prompt 模板。
支持 {placeholder} 占位符替换。
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

# prompts 目录相对于项目根
_PROMPTS_DIR = Path(__file__).parent.parent / "prompts"


def load_prompt(
    name: str,
    version: str = "v0.1",
    **kwargs: str,
) -> str:
    """
    加载指定名称和版本的 Prompt 模板。

    Args:
        name: Prompt 名称（如 extractor, reducer, schema_genesis）
        version: 版本号（如 v0.1, v0.2）
        **kwargs: 占位符替换（如 schema_constraint="..."）

    Returns:
        填充后的 Prompt 文本

    Example:
        load_prompt("extractor", "v0.2", schema_constraint="输出 YAML...")
    """
    filename = f"{name}_{version}.md"
    filepath = _PROMPTS_DIR / filename

    if not filepath.exists():
        raise FileNotFoundError(
            f"Prompt 模板不存在：{filepath}\n"
            f"可用模板：{list_prompt_versions(name)}"
        )

    template = filepath.read_text(encoding="utf-8")

    # 替换占位符
    for key, value in kwargs.items():
        placeholder = "{" + key + "}"
        template = template.replace(placeholder, value)

    return template


def list_prompt_versions(name: str) -> list[str]:
    """列出指定名称的所有可用版本"""
    if not _PROMPTS_DIR.exists():
        return []
    return sorted(
        f.stem.replace(f"{name}_", "")
        for f in _PROMPTS_DIR.glob(f"{name}_*.md")
    )


def list_all_prompts() -> dict[str, list[str]]:
    """列出所有 Prompt 模板及其版本"""
    if not _PROMPTS_DIR.exists():
        return {}

    prompts: dict[str, list[str]] = {}
    for f in sorted(_PROMPTS_DIR.glob("*.md")):
        parts = f.stem.rsplit("_", 1)
        if len(parts) == 2:
            name, version = parts
            prompts.setdefault(name, []).append(version)

    return prompts
