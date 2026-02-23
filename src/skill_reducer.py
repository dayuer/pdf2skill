"""
Skill 去重与合并器 — Phase 3: Reduce + Critic

职责：
1. 向量编码所有 Skill 的 trigger + body
2. 余弦相似度聚类（阈值 0.88）
3. R1 合并同类项 + 审查逻辑闭环
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from .config import config
from .llm_client import AsyncDeepSeekClient, DeepSeekClient
from .skill_validator import ValidatedSkill


@dataclass
class SkillCluster:
    """同主题 Skill 聚类"""

    skills: list[ValidatedSkill]
    centroid_idx: int = 0  # 该簇中最具代表性的 Skill 索引
    # 融合状态矩阵：记录合并溯源
    merge_log: list[str] = field(default_factory=list)
    # Tag 归一化映射（原始 → 归一化后）
    tag_map: dict[str, str] = field(default_factory=dict)


# ──── Tag 归一化 ────

# 领域同义词库：值 → 标准化标签
_DOMAIN_SYNONYMS: dict[str, str] = {
    # 保险领域
    "保险": "保险", "insurance": "保险", "保障": "保险",
    "理赔": "保险·理赔", "赔付": "保险·理赔", "claims": "保险·理赔",
    "核保": "保险·核保", "承保": "保险·核保", "underwriting": "保险·核保",
    # 法律领域
    "法律": "法律", "法规": "法律", "legal": "法律", "法务": "法律",
    "合同": "法律·合同", "contract": "法律·合同", "条款": "法律·合同",
    # 技术领域
    "技术": "技术", "technology": "技术", "tech": "技术",
    "开发": "技术·开发", "编程": "技术·开发", "programming": "技术·开发",
    "运维": "技术·运维", "devops": "技术·运维", "ops": "技术·运维",
    # 医学领域
    "医学": "医学", "medical": "医学", "临床": "医学·临床",
    "药学": "医学·药学", "pharmacy": "医学·药学",
    # 金融领域
    "金融": "金融", "finance": "金融", "财务": "金融",
    "投资": "金融·投资", "investment": "金融·投资",
}


def normalize_domain(domain: str) -> str:
    """将 domain 标签归一化为标准形式"""
    key = domain.lower().strip()
    return _DOMAIN_SYNONYMS.get(key, domain)


def normalize_skills_tags(skills: list[ValidatedSkill]) -> dict[str, str]:
    """
    批量归一化所有 Skill 的 domain 标签。

    返回原始 → 归一化的映射表。
    """
    tag_map: dict[str, str] = {}
    for skill in skills:
        original = skill.domain
        normalized = normalize_domain(original)
        if original != normalized:
            tag_map[original] = normalized
            skill.domain = normalized
    return tag_map


# ──── 向量化（轻量级，不依赖外部 Embedding 模型） ────


def _text_to_vector(text: str, vocab: dict[str, int], dim: int) -> np.ndarray:
    """
    简易 TF 向量化（不依赖 Embedding 模型）。

    在 Skill 数量 < 500 的场景下，基于词频的 TF 向量已足够用于去重，
    不需要引入 bge-m3 等重型 Embedding 模型。
    """
    vec = np.zeros(dim, dtype=np.float32)
    words = text.lower().split()
    for w in words:
        if w in vocab:
            vec[vocab[w]] += 1
    # L2 归一化
    norm = np.linalg.norm(vec)
    if norm > 0:
        vec /= norm
    return vec


def _build_vocab(texts: list[str], max_dim: int = 2000) -> dict[str, int]:
    """从文本列表中构建词汇表"""
    word_freq: dict[str, int] = {}
    for text in texts:
        for w in text.lower().split():
            word_freq[w] = word_freq.get(w, 0) + 1

    # 按频率排序，取前 max_dim 个
    sorted_words = sorted(word_freq.items(), key=lambda x: -x[1])[:max_dim]
    return {w: i for i, (w, _) in enumerate(sorted_words)}


# ──── 聚类 ────


def cluster_skills(
    skills: list[ValidatedSkill],
    threshold: float | None = None,
    use_vector_store: bool = False,
) -> list[SkillCluster]:
    """
    基于余弦相似度的贪心聚类。

    Args:
        skills: 校验通过的 Skill 列表
        threshold: 相似度阈值（默认使用配置值 0.88）
        use_vector_store: 使用 Milvus-Lite Embedding 向量化（需 Embedding 配置）

    Returns:
        聚类结果列表
    """
    if not skills:
        return []

    if threshold is None:
        threshold = config.dedup_similarity_threshold

    # Tag 归一化：先统一 domain 再聚类
    tag_map = normalize_skills_tags(skills)

    # 构建每个 Skill 的文本表示（trigger + body 前 500 字）
    texts = [f"{s.trigger} {s.body[:500]}" for s in skills]

    # 向量化策略：优先 Embedding，降级为 TF
    vectors = None
    if use_vector_store and config.embedding.is_configured:
        try:
            from openai import OpenAI
            client = OpenAI(
                api_key=config.embedding.api_key,
                base_url=config.embedding.base_url,
            )
            resp = client.embeddings.create(input=texts, model=config.embedding.model)
            vectors = np.array([d.embedding for d in resp.data], dtype=np.float32)
            import logging
            logging.getLogger(__name__).info(
                "✅ 聚类使用 Embedding 向量化（%d 维）", vectors.shape[1]
            )
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"Embedding 降级为 TF 向量: {e}")

    if vectors is None:
        vocab = _build_vocab(texts)
        dim = len(vocab) if vocab else 1
        vectors = np.array([_text_to_vector(t, vocab, dim) for t in texts])

    # 贪心聚类
    used = [False] * len(skills)
    clusters: list[SkillCluster] = []

    for i in range(len(skills)):
        if used[i]:
            continue

        cluster_skills_list = [skills[i]]
        used[i] = True

        for j in range(i + 1, len(skills)):
            if used[j]:
                continue

            # 余弦相似度
            sim = float(np.dot(vectors[i], vectors[j]))
            if sim >= threshold:
                cluster_skills_list.append(skills[j])
                used[j] = True

        clusters.append(SkillCluster(
            skills=cluster_skills_list,
            tag_map=tag_map,
            merge_log=[f"初始聚类: {len(cluster_skills_list)} 个 Skill"],
        ))

    return clusters


# ──── R1 Reduce + Critic ────

_REDUCE_SYSTEM_PROMPT = """你是一个严苛的知识审查官。你的任务是将多个相似的操作规范合并为一个最终版本。

合并规则：
1. 消除重复步骤，保留最完整的描述
2. 如果不同规范存在矛盾的判断条件，以来源章节更权威的为准，在输出中标注冲突来源
3. 审查合并后的逻辑闭环：
   - 每个步骤是否有明确的前置条件？
   - 是否存在未处理的分支（如只有 IF 没有 ELSE）？
   - 是否缺少错误处理或回退方案？
4. 如果发现缺陷，在输出末尾用 [CRITIC] 标注具体问题

输出格式严格使用 YAML Frontmatter + Markdown Body，与输入格式完全一致。
直接输出合并后的 Skill，禁止输出分析过程。"""

_REDUCE_USER_PROMPT = """以下是 {count} 个相似的 Skill，需要合并为一个最终版本：

{skills_text}

请合并以上 Skill，消除重复，补充遗漏，输出一个完整的最终 Skill。"""


async def reduce_cluster(
    cluster: SkillCluster,
    *,
    client: Optional[AsyncDeepSeekClient] = None,
) -> ValidatedSkill:
    """
    将一个聚类中的多个 Skill 合并为一个。

    如果聚类只有一个 Skill，直接返回。
    """
    if len(cluster.skills) == 1:
        return cluster.skills[0]

    if client is None:
        client = AsyncDeepSeekClient()

    # 拼接所有 Skill 文本
    skills_text = "\n\n---\n\n".join(
        f"### Skill {i + 1}（来源：{s.source_context}）\n\n{s.raw_text}"
        for i, s in enumerate(cluster.skills)
    )

    response = await client.chat(
        system_prompt=_REDUCE_SYSTEM_PROMPT,
        user_prompt=_REDUCE_USER_PROMPT.format(
            count=len(cluster.skills),
            skills_text=skills_text,
        ),
    )

    # 记录日志
    client.logger.log(
        phase="reduce_critic",
        prompt_version="v0.1",
        system_prompt=_REDUCE_SYSTEM_PROMPT,
        user_prompt=skills_text[:500],
        response=response,
        input_metadata={
            "cluster_size": len(cluster.skills),
            "source_indices": [s.source_chunk_index for s in cluster.skills],
        },
    )

    # 合并后的 Skill 继承第一个 Skill 的元信息
    first = cluster.skills[0]

    # 记录融合状态
    cluster.merge_log.append(
        f"R1 合并: {len(cluster.skills)} 个 Skill → 1 | "
        f"来源 chunks: {[s.source_chunk_index for s in cluster.skills]}"
    )

    return ValidatedSkill(
        name=first.name,
        trigger=first.trigger,
        domain=first.domain,
        prerequisites=first.prerequisites,
        source_ref=first.source_ref,
        confidence=first.confidence,
        body=response.content,
        raw_text=response.content,
        source_chunk_index=first.source_chunk_index,
        source_context=first.source_context,
    )


async def reduce_all_clusters(
    clusters: list[SkillCluster],
    *,
    client: Optional[AsyncDeepSeekClient] = None,
) -> list[ValidatedSkill]:
    """批量合并所有聚类"""
    if client is None:
        client = AsyncDeepSeekClient()

    tasks = [reduce_cluster(c, client=client) for c in clusters]
    return await asyncio.gather(*tasks)
