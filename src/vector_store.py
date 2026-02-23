"""
Milvus-Lite 向量存储 — Skill 语义检索与去重。

使用本地文件模式（无需外部服务），为 Skill 的 trigger + body
提供向量化存储和相似度检索能力。

当 Embedding 未配置时自动降级为不可用状态（is_available = False）。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from .config import config

logger = logging.getLogger(__name__)


class SkillVectorStore:
    """
    Skill 向量存储 — 基于 Milvus-Lite 单文件模式。

    提供三个核心能力：
    1. add_skills — 将 Skill 向量化后写入 Milvus
    2. search_similar — 语义相似检索
    3. find_duplicates — 批量去重
    """

    COLLECTION_NAME = "skill_vectors"

    def __init__(self, db_path: Optional[str] = None) -> None:
        self._milvus = None
        self._openai = None
        self._db_path = db_path or config.milvus_db_path
        self._dim = config.embedding.dim
        self._available = False

        if config.embedding.is_configured:
            self._init_clients()

    @property
    def is_available(self) -> bool:
        """向量存储是否可用（Embedding 已配置且 Milvus 初始化成功）"""
        return self._available

    def _init_clients(self) -> None:
        """懒初始化 Milvus 和 OpenAI 客户端"""
        try:
            from openai import OpenAI
            from pymilvus import MilvusClient

            # 确保目录存在
            Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)

            self._milvus = MilvusClient(uri=self._db_path)
            self._openai = OpenAI(
                api_key=config.embedding.api_key,
                base_url=config.embedding.base_url,
            )

            # 创建集合（如果不存在）
            if not self._milvus.has_collection(self.COLLECTION_NAME):
                self._milvus.create_collection(
                    collection_name=self.COLLECTION_NAME,
                    dimension=self._dim,
                    metric_type="COSINE",
                    auto_id=True,
                    enable_dynamic_field=True,
                )
                logger.info(f"✅ 创建 Milvus 集合: {self.COLLECTION_NAME}")

            self._available = True
            logger.info(f"✅ 向量存储初始化成功: {self._db_path}")
        except Exception as e:
            logger.warning(f"向量存储初始化失败（降级为不可用）: {e}")
            self._available = False

    def _get_embeddings(self, texts: list[str]) -> list[list[float]]:
        """批量获取 Embedding 向量"""
        if not self._openai:
            raise RuntimeError("OpenAI 客户端未初始化")
        resp = self._openai.embeddings.create(
            input=texts,
            model=config.embedding.model,
        )
        return [d.embedding for d in resp.data]

    def add_skills(self, skills: list[dict]) -> int:
        """
        将 Skill 列表向量化后写入 Milvus。

        Args:
            skills: Skill 字典列表，需包含 name, trigger, body 字段

        Returns:
            实际写入的数量
        """
        if not self.is_available or not skills:
            return 0

        texts = [
            f"{s.get('trigger', '')} {s.get('body', '')[:500]}"
            for s in skills
        ]
        embeddings = self._get_embeddings(texts)

        data = [
            {
                "vector": vec,
                "name": s.get("name", ""),
                "trigger": s.get("trigger", ""),
                "domain": s.get("domain", ""),
                "sku_type": s.get("sku_type", ""),
            }
            for s, vec in zip(skills, embeddings)
        ]

        self._milvus.insert(
            collection_name=self.COLLECTION_NAME,
            data=data,
        )
        logger.info(f"✅ 写入 {len(data)} 个 Skill 向量")
        return len(data)

    def search_similar(
        self,
        query: str,
        top_k: int = 5,
    ) -> list[dict]:
        """
        语义相似检索。

        Args:
            query: 查询文本
            top_k: 返回数量

        Returns:
            匹配结果列表，含 name, trigger, domain, score
        """
        if not self.is_available:
            return []

        q_vec = self._get_embeddings([query])[0]
        results = self._milvus.search(
            collection_name=self.COLLECTION_NAME,
            data=[q_vec],
            limit=top_k,
            output_fields=["name", "trigger", "domain", "sku_type"],
        )

        if not results or not results[0]:
            return []

        return [
            {
                "name": hit["entity"].get("name", ""),
                "trigger": hit["entity"].get("trigger", ""),
                "domain": hit["entity"].get("domain", ""),
                "sku_type": hit["entity"].get("sku_type", ""),
                "score": round(hit["distance"], 4),
            }
            for hit in results[0]
        ]

    def find_duplicates(self, threshold: float = 0.92) -> list[tuple[str, str, float]]:
        """
        批量去重：找出相似度超过阈值的 Skill 对。

        Returns:
            (skill_a, skill_b, similarity) 三元组列表
        """
        if not self.is_available:
            return []

        # 查询所有向量
        all_data = self._milvus.query(
            collection_name=self.COLLECTION_NAME,
            filter="",
            output_fields=["name", "vector"],
            limit=10000,
        )

        if not all_data:
            return []

        duplicates = []
        seen = set()
        for item in all_data:
            name = item.get("name", "")
            vec = item.get("vector")
            if not vec or not name:
                continue

            results = self._milvus.search(
                collection_name=self.COLLECTION_NAME,
                data=[vec],
                limit=5,
                output_fields=["name"],
            )

            if results and results[0]:
                for hit in results[0]:
                    other_name = hit["entity"].get("name", "")
                    score = hit["distance"]
                    if other_name != name and score >= threshold:
                        pair = tuple(sorted([name, other_name]))
                        if pair not in seen:
                            seen.add(pair)
                            duplicates.append((pair[0], pair[1], round(score, 4)))

        return sorted(duplicates, key=lambda x: x[2], reverse=True)

    def clear(self) -> None:
        """清空向量存储"""
        if self.is_available and self._milvus:
            if self._milvus.has_collection(self.COLLECTION_NAME):
                self._milvus.drop_collection(self.COLLECTION_NAME)
                logger.info("🗑️ 向量存储已清空")
