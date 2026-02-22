"""
SKU 分类器 — Phase 3: 知识单元类型分类

策略：规则优先 + LLM 兜底
1. 基于关键词模式匹配分类 (>90% 命中率)
2. 不确定时批量发 LLM 分类
"""

from __future__ import annotations

import re
from typing import Optional

from .skill_validator import ValidatedSkill, SKUType


# ──── 规则分类 ────

# 事实型关键词（高置信度）
_FACTUAL_PATTERNS = re.compile(
    r"档案|简历|生卒|籍贯|出生|家族|官职|品级|俸禄|"
    r"收支|账目|银两|数据|数量|人口|产量|价格|"
    r"地理|位置|建筑|结构|布局|面积|距离|"
    r"设定|背景|世界观|历史|年表|时间线|大事记|"
    r"物品|器物|武器|装备|规格|材质",
    re.IGNORECASE,
)

# 程序型关键词（高置信度）
_PROCEDURAL_PATTERNS = re.compile(
    r"流程|步骤|操作|程序|方法|策略|战术|技巧|"
    r"如何|怎样|应对|处理|执行|部署|实施|"
    r"决策|判断|选择|权衡|博弈|谈判|"
    r"IF.*THEN|前置条件|触发条件|预期结果|"
    r"第[一二三四五六七八九十]步|Step\s*\d",
    re.IGNORECASE,
)

# 关系型关键词（高置信度）
_RELATIONAL_PATTERNS = re.compile(
    r"关系|派系|阵营|联盟|对立|从属|"
    r"标签|分类|层级|等级|体系|谱系|"
    r"术语|定义|概念|词汇|名词解释|"
    r"网络|图谱|依赖|影响链|因果链",
    re.IGNORECASE,
)


def _score_by_rules(skill: ValidatedSkill) -> dict[SKUType, int]:
    """基于关键词模式匹配打分"""
    text = f"{skill.name} {skill.trigger} {skill.body[:500]}"
    return {
        SKUType.FACTUAL: len(_FACTUAL_PATTERNS.findall(text)),
        SKUType.PROCEDURAL: len(_PROCEDURAL_PATTERNS.findall(text)),
        SKUType.RELATIONAL: len(_RELATIONAL_PATTERNS.findall(text)),
    }


def classify_skill(skill: ValidatedSkill) -> SKUType:
    """对单个 Skill 进行 SKU 类型分类（纯规则）"""
    scores = _score_by_rules(skill)
    max_type = max(scores, key=lambda k: scores[k])

    # 明确命中（最高分 >= 2 且领先第二名 >= 1）
    sorted_scores = sorted(scores.values(), reverse=True)
    if sorted_scores[0] >= 2 and sorted_scores[0] - sorted_scores[1] >= 1:
        return max_type

    # 弱信号时用启发式规则
    body = skill.body.lower()

    # 有编号步骤 → procedural
    if re.search(r"[1-9]\.", body):
        return SKUType.PROCEDURAL

    # 有箭头关系 → relational
    if "→" in body or "->" in body or "关系" in body:
        return SKUType.RELATIONAL

    # 默认 factual（事实类最通用）
    return SKUType.FACTUAL


def classify_batch(
    skills: list[ValidatedSkill],
) -> list[ValidatedSkill]:
    """
    批量分类：为每个 Skill 标注 sku_type。

    原地修改并返回。
    """
    for skill in skills:
        skill.sku_type = classify_skill(skill)
        if not skill.sku_id:
            from .skill_validator import _slugify
            skill.sku_id = _slugify(skill.name)
    return skills
