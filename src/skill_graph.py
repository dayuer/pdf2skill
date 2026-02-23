"""
Skill 知识图谱 — 基于 NetworkX 分析 Skill 间关系与重要性。

流程：LLM 抽取 Skill 间关系 → NetworkX 构建有向图 →
      PageRank 计算重要性 → 度中心性找核心 Skill → 输出 Mermaid/JSON。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

import networkx as nx

from .config import config
from .llm_client import DeepSeekClient

logger = logging.getLogger(__name__)


@dataclass
class GraphAnalysis:
    """图谱分析结果"""

    top_skills: list[dict[str, Any]] = field(default_factory=list)
    clusters: list[list[str]] = field(default_factory=list)
    statistics: dict[str, Any] = field(default_factory=dict)
    mermaid: str = ""
    graph_json: dict[str, Any] = field(default_factory=dict)


_RELATION_PROMPT = """分析以下 Skill 列表之间的关系。每个 Skill 有 name（名称）和 domain（领域）。

Skill 列表：
{skills_text}

请提取 Skill 之间的关系（如依赖、互补、同域、父子等），以 JSON 格式返回：
{{
  "relations": [
    {{"source": "SkillA名称", "target": "SkillB名称", "relation": "依赖", "weight": 0.9}},
    {{"source": "SkillC名称", "target": "SkillD名称", "relation": "同域", "weight": 0.7}}
  ]
}}

规则：
- source/target 必须是上面列表中的 Skill 名称
- relation 可选值：依赖、互补、同域、包含、前置
- weight 范围 0-1，表示关系强度
- 只提取有意义的关系，不要强行配对
- 只返回 JSON，无其他内容"""


class SkillGraphBuilder:
    """
    Skill 关系图谱构建器。

    使用 LLM 抽取 Skill 间关系，通过 NetworkX 图算法分析
    Skill 的重要性排序和主题聚类。
    """

    def __init__(self, client: Optional[DeepSeekClient] = None) -> None:
        self.graph = nx.DiGraph()
        self._client = client

    @property
    def client(self) -> DeepSeekClient:
        if self._client is None:
            self._client = DeepSeekClient()
        return self._client

    def build_from_skills(self, skills: list[dict[str, Any]]) -> None:
        """
        从 Skill 列表构建图谱。

        Args:
            skills: Skill 字典列表，每项需包含 name, domain, trigger 字段
        """
        if not skills:
            return

        # 1. 添加所有 Skill 作为节点
        for s in skills:
            self.graph.add_node(
                s["name"],
                domain=s.get("domain", ""),
                trigger=s.get("trigger", ""),
                sku_type=s.get("sku_type", ""),
            )

        # 2. 自动添加同域边（轻量级，不需 LLM）
        by_domain: dict[str, list[str]] = {}
        for s in skills:
            domain = s.get("domain", "")
            by_domain.setdefault(domain, []).append(s["name"])

        for domain, names in by_domain.items():
            if len(names) > 1:
                for i, a in enumerate(names):
                    for b in names[i + 1 :]:
                        if not self.graph.has_edge(a, b):
                            self.graph.add_edge(
                                a, b, relation="同域", weight=0.5
                            )

        # 3. LLM 抽取深层关系（批次处理，每批 30 个）
        batch_size = 30
        for offset in range(0, len(skills), batch_size):
            batch = skills[offset : offset + batch_size]
            self._extract_relations_batch(batch)

        logger.info(
            f"✅ 图谱构建完成：{self.graph.number_of_nodes()} 节点, "
            f"{self.graph.number_of_edges()} 边"
        )

    def _extract_relations_batch(self, skills: list[dict[str, Any]]) -> None:
        """通过 LLM 批量抽取 Skill 间关系"""
        skills_text = "\n".join(
            f"- {s['name']} (领域: {s.get('domain', '未知')})"
            for s in skills
        )

        prompt = _RELATION_PROMPT.format(skills_text=skills_text)
        valid_names = {s["name"] for s in skills}

        try:
            response = self.client.call_sync(
                instructions="你是知识图谱构建专家，精确提取实体间关系。",
                prompt=prompt,
            )
            text = response.replace("```json", "").replace("```", "").strip()
            data = json.loads(text)

            for rel in data.get("relations", []):
                src, tgt = rel.get("source", ""), rel.get("target", "")
                if src in valid_names and tgt in valid_names and src != tgt:
                    self.graph.add_edge(
                        src,
                        tgt,
                        relation=rel.get("relation", "相关"),
                        weight=min(max(rel.get("weight", 0.5), 0), 1),
                    )
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning(f"LLM 关系抽取解析失败: {e}")
        except Exception as e:
            logger.error(f"LLM 关系抽取异常: {e}")

    def analyze(self) -> GraphAnalysis:
        """
        执行图算法分析。

        返回包含 Top Skills、聚类、统计、Mermaid 图和 JSON 的完整分析结果。
        """
        if self.graph.number_of_nodes() == 0:
            return GraphAnalysis()

        # 1. PageRank 计算重要性
        try:
            pagerank = nx.pagerank(self.graph, weight="weight")
        except nx.NetworkXException:
            pagerank = {n: 1.0 / self.graph.number_of_nodes() for n in self.graph.nodes}

        top_skills = sorted(pagerank.items(), key=lambda x: x[1], reverse=True)[:15]

        # 2. 度中心性
        degree_cent = nx.degree_centrality(self.graph)

        # 3. 聚类（连通分量）
        undirected = self.graph.to_undirected()
        clusters = [
            sorted(comp)
            for comp in nx.connected_components(undirected)
            if len(comp) > 1
        ]
        clusters.sort(key=len, reverse=True)

        # 4. 统计
        stats = {
            "total_nodes": self.graph.number_of_nodes(),
            "total_edges": self.graph.number_of_edges(),
            "clusters": len(clusters),
            "avg_degree": round(
                sum(dict(self.graph.degree()).values()) / max(self.graph.number_of_nodes(), 1),
                2,
            ),
        }

        return GraphAnalysis(
            top_skills=[
                {
                    "name": name,
                    "pagerank": round(score, 4),
                    "degree_centrality": round(degree_cent.get(name, 0), 4),
                    "domain": self.graph.nodes[name].get("domain", ""),
                }
                for name, score in top_skills
            ],
            clusters=clusters[:10],
            statistics=stats,
            mermaid=self._to_mermaid(),
            graph_json=self._to_json(),
        )

    def _to_mermaid(self) -> str:
        """导出 Mermaid 图谱"""
        lines = ["graph LR"]
        node_ids: dict[str, str] = {}

        for i, node in enumerate(self.graph.nodes):
            nid = f"N{i}"
            node_ids[node] = nid
            # 截断过长的名称
            label = node[:20] + "…" if len(node) > 20 else node
            lines.append(f'    {nid}["{label}"]')

        for u, v, data in self.graph.edges(data=True):
            rel = data.get("relation", "")
            uid, vid = node_ids.get(u, ""), node_ids.get(v, "")
            if uid and vid:
                if rel:
                    lines.append(f"    {uid} -->|{rel}| {vid}")
                else:
                    lines.append(f"    {uid} --> {vid}")

        return "\n".join(lines)

    def _to_json(self) -> dict[str, Any]:
        """导出图谱 JSON"""
        return {
            "nodes": [
                {
                    "id": node,
                    "domain": data.get("domain", ""),
                    "trigger": data.get("trigger", ""),
                }
                for node, data in self.graph.nodes(data=True)
            ],
            "edges": [
                {
                    "source": u,
                    "target": v,
                    "relation": data.get("relation", ""),
                    "weight": data.get("weight", 0.5),
                }
                for u, v, data in self.graph.edges(data=True)
            ],
        }
