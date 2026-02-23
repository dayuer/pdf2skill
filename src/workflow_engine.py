"""
工作流引擎 — 接收 JSON DAG 定义，按拓扑序执行各节点。

JSON 格式:
{
  "nodes": [
    {"id": "load", "type": "document_loader", "config": {}},
    {"id": "chunk", "type": "chunker", "config": {"max_chars": 2000}},
    ...
  ],
  "edges": [
    {"source": "load", "target": "chunk"},
    ...
  ]
}
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

logger = logging.getLogger(__name__)


class NodeStatus(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"
    SKIPPED = "skipped"


@dataclass
class WorkflowNode:
    """单个工作流节点"""
    id: str
    type: str
    label: str = ""
    config: dict = field(default_factory=dict)
    status: NodeStatus = NodeStatus.IDLE
    result: Any = None
    error: str | None = None
    elapsed_s: float = 0.0


@dataclass
class WorkflowRun:
    """一次工作流执行的状态"""
    run_id: str
    nodes: dict[str, WorkflowNode] = field(default_factory=dict)
    edges: list[tuple[str, str]] = field(default_factory=list)
    status: str = "pending"
    started_at: float = 0.0
    elapsed_s: float = 0.0


# ── 步骤类型到执行函数的注册表 ──
_STEP_REGISTRY: dict[str, Callable] = {}


def register_step(step_type: str):
    """装饰器：注册步骤执行函数"""
    def decorator(fn):
        _STEP_REGISTRY[step_type] = fn
        return fn
    return decorator


def _topo_sort(nodes: list[str], edges: list[tuple[str, str]]) -> list[str]:
    """拓扑排序"""
    in_degree = defaultdict(int)
    graph = defaultdict(list)
    node_set = set(nodes)

    for src, dst in edges:
        if src in node_set and dst in node_set:
            graph[src].append(dst)
            in_degree[dst] += 1

    queue = deque(n for n in nodes if in_degree[n] == 0)
    result = []

    while queue:
        node = queue.popleft()
        result.append(node)
        for neighbor in graph[node]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    if len(result) != len(node_set):
        raise ValueError("工作流存在循环依赖")

    return result


class WorkflowEngine:
    """工作流引擎 — 按拓扑序执行 DAG"""

    def __init__(self):
        self._runs: dict[str, WorkflowRun] = {}

    def parse(self, workflow_json: dict) -> WorkflowRun:
        """解析 JSON 为 WorkflowRun"""
        run_id = f"run-{int(time.time() * 1000)}"
        run = WorkflowRun(run_id=run_id)

        for nd in workflow_json.get("nodes", []):
            run.nodes[nd["id"]] = WorkflowNode(
                id=nd["id"],
                type=nd.get("type", "unknown"),
                label=nd.get("label", nd["id"]),
                config=nd.get("config", {}),
            )

        for edge in workflow_json.get("edges", []):
            run.edges.append((edge["source"], edge["target"]))

        self._runs[run_id] = run
        return run

    async def execute(
        self,
        run: WorkflowRun,
        context: dict,
        on_status: Callable[[str, NodeStatus, Any], None] | None = None,
    ) -> WorkflowRun:
        """
        执行工作流。

        Args:
            run: 解析后的工作流
            context: 共享上下文（notebook_id、FileNotebook 等）
            on_status: 状态回调 (node_id, status, data)
        """
        run.status = "running"
        run.started_at = time.time()

        node_ids = list(run.nodes.keys())
        order = _topo_sort(node_ids, run.edges)

        logger.info(f"[{run.run_id}] 执行顺序: {order}")

        for node_id in order:
            node = run.nodes[node_id]
            step_fn = _STEP_REGISTRY.get(node.type)

            if not step_fn:
                logger.warning(f"[{run.run_id}] 未注册步骤类型: {node.type}，跳过")
                node.status = NodeStatus.SKIPPED
                if on_status:
                    on_status(node_id, NodeStatus.SKIPPED, None)
                continue

            node.status = NodeStatus.RUNNING
            if on_status:
                on_status(node_id, NodeStatus.RUNNING, None)

            t0 = time.time()
            try:
                # 收集上游结果
                upstream_results = {}
                for src, dst in run.edges:
                    if dst == node_id and src in run.nodes:
                        upstream_results[src] = run.nodes[src].result

                result = await step_fn(
                    node=node,
                    context=context,
                    upstream=upstream_results,
                )
                node.result = result
                node.status = NodeStatus.DONE
                node.elapsed_s = round(time.time() - t0, 2)

                if on_status:
                    on_status(node_id, NodeStatus.DONE, {
                        "elapsed_s": node.elapsed_s,
                        "summary": _summarize(result),
                    })

                logger.info(f"[{run.run_id}] {node_id} 完成 ({node.elapsed_s}s)")

            except Exception as e:
                node.status = NodeStatus.ERROR
                node.error = str(e)
                node.elapsed_s = round(time.time() - t0, 2)

                if on_status:
                    on_status(node_id, NodeStatus.ERROR, {"error": str(e)})

                logger.error(f"[{run.run_id}] {node_id} 失败: {e}")
                # 继续执行后续节点（非阻塞模式）

        run.status = "completed"
        run.elapsed_s = round(time.time() - run.started_at, 2)
        return run

    def get_run(self, run_id: str) -> WorkflowRun | None:
        return self._runs.get(run_id)

    def to_json(self, run: WorkflowRun) -> dict:
        """序列化执行结果"""
        return {
            "run_id": run.run_id,
            "status": run.status,
            "elapsed_s": run.elapsed_s,
            "nodes": {
                nid: {
                    "id": n.id,
                    "type": n.type,
                    "label": n.label,
                    "status": n.status.value,
                    "elapsed_s": n.elapsed_s,
                    "error": n.error,
                    "summary": _summarize(n.result),
                }
                for nid, n in run.nodes.items()
            },
        }


def _summarize(result: Any) -> str | None:
    """简要摘要结果"""
    if result is None:
        return None
    if isinstance(result, dict):
        if "skills" in result:
            return f"{len(result['skills'])} skills"
        if "chunks" in result:
            return f"{len(result['chunks'])} chunks"
        return f"{len(result)} keys"
    if isinstance(result, list):
        return f"{len(result)} items"
    return str(result)[:100]


# ══════ 注册默认步骤 ══════

@register_step("document_loader")
async def step_load(node: WorkflowNode, context: dict, upstream: dict):
    """文档加载（上传时已完成，直接返回 meta）"""
    fs = context.get("notebook")
    if fs:
        return fs.load_meta()
    return {"status": "skipped", "reason": "已在上传阶段完成"}


@register_step("chunker")
async def step_chunk(node: WorkflowNode, context: dict, upstream: dict):
    """切分（上传时已完成）"""
    fs = context.get("notebook")
    if fs:
        chunks = fs.load_chunks()
        return {"chunks": chunks, "count": len(chunks)}
    return {"status": "skipped"}


@register_step("semantic_filter")
async def step_filter(node: WorkflowNode, context: dict, upstream: dict):
    """语义密度过滤（上传时已完成）"""
    return {"status": "done", "reason": "上传时自动完成"}


@register_step("schema_gen")
async def step_schema(node: WorkflowNode, context: dict, upstream: dict):
    """Schema 生成 — 保存 system prompt"""
    fs = context.get("notebook")
    prompt = node.config.get("system_prompt", "")
    if fs and prompt:
        meta = fs.load_meta()
        meta["system_prompt"] = prompt
        fs.save_meta(meta)
    return {"system_prompt_saved": bool(prompt)}


@register_step("extractor")
async def step_extract(node: WorkflowNode, context: dict, upstream: dict):
    """技能提取 — 调用 tune 接口逻辑"""
    # 实际提取逻辑通过现有 pipeline 执行
    return {"status": "delegated", "reason": "通过 tune/execute API 执行"}


@register_step("validator")
async def step_validate(node: WorkflowNode, context: dict, upstream: dict):
    """校验 — 调用 sample-check 逻辑"""
    return {"status": "delegated", "reason": "通过 sample-check API 执行"}


@register_step("reducer")
async def step_reduce(node: WorkflowNode, context: dict, upstream: dict):
    """聚类去重"""
    return {"status": "delegated", "reason": "在全量执行中自动触发"}


@register_step("classifier")
async def step_classify(node: WorkflowNode, context: dict, upstream: dict):
    """SKU 分类"""
    return {"status": "delegated", "reason": "在全量执行中自动触发"}


@register_step("packager")
async def step_package(node: WorkflowNode, context: dict, upstream: dict):
    """打包输出"""
    return {"status": "delegated", "reason": "在全量执行中自动触发"}
