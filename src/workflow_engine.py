"""
工作流引擎 — n8n 式 BFS 执行器。

核心机制（对标 n8n packages/core/workflow-execute.ts）：

1. 解析 WorkflowDefinition → 构建双向连接索引
2. 初始化 node_execution_stack（入度为 0 的节点入栈）
3. 主循环：从栈中取出节点 → 执行 → 推送下游
   - 下游所有输入就绪 → 入栈
   - 下游仍在等待 → 放入 waiting_execution
4. 栈空 → 完成

与旧版差异：
- 旧版：topo_sort → for 循环顺序执行
- 新版：BFS 栈 + waiting 队列，支持多输出/错误分支/pinData
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from typing import Any, Callable, Awaitable

from .workflow_types import (
    ConnectionType,
    ExecuteData,
    ExecutionContext,
    ExecutionStatus,
    NodeExecutionData,
    NodeStatus,
    WorkflowDefinition,
    WorkflowNode,
)

logger = logging.getLogger(__name__)


# ═══════ 步骤注册表 ═══════

StepFunction = Callable[
    ["WorkflowNode", dict[str, Any], NodeExecutionData],
    Awaitable[NodeExecutionData | dict[str, list[NodeExecutionData | None]]]
]

_STEP_REGISTRY: dict[str, StepFunction] = {}


def register_step(step_type: str):
    """装饰器：注册步骤执行函数。

    函数签名: async (node, context, input_data) -> NodeExecutionData | dict
    - 返回 NodeExecutionData：作为 main[0] 输出
    - 返回 dict：多输出（{"main": [...], "error": [...]}）
    """
    def decorator(fn: StepFunction) -> StepFunction:
        _STEP_REGISTRY[step_type] = fn
        return fn
    return decorator


# ═══════ Workflow（运行时图结构）═══════

class Workflow:
    """运行时工作流 — 对标 n8n Workflow 类。

    持有 nodes dict + 双向连接索引。
    """

    def __init__(self, definition: WorkflowDefinition):
        self.id = definition.id
        self.name = definition.name
        self.settings = definition.settings
        self.pin_data = definition.pin_data

        # 解析 nodes
        self.nodes: dict[str, WorkflowNode] = {}
        for nd in definition.nodes:
            node = WorkflowNode(
                id=nd["id"],
                type=nd.get("type", "unknown"),
                label=nd.get("label", nd["id"]),
                icon=nd.get("icon", ""),
                desc=nd.get("desc", ""),
                position=tuple(nd.get("position", {}).values()) if isinstance(nd.get("position"), dict)
                    else tuple(nd.get("position", (0, 0))),
                parameters=nd.get("parameters", nd.get("config", {})),
                disabled=nd.get("disabled", False),
            )
            self.nodes[node.id] = node

        # 构建双向连接索引
        # connections_by_source[src][output_type] = [(target, input_index)]
        self.connections_by_source: dict[str, dict[str, list[tuple[str, int]]]] = defaultdict(
            lambda: defaultdict(list)
        )
        # connections_by_dest[dst][input_type] = [(source, output_index)]
        self.connections_by_dest: dict[str, dict[str, list[tuple[str, int]]]] = defaultdict(
            lambda: defaultdict(list)
        )

        for conn in definition.connections:
            src = conn.get("source", "")
            dst = conn.get("target", "")
            out_type = conn.get("sourceOutputType", ConnectionType.MAIN)
            out_idx = conn.get("sourceOutputIndex", 0)
            in_type = conn.get("targetInputType", ConnectionType.MAIN)
            in_idx = conn.get("targetInputIndex", 0)

            if src and dst and src in self.nodes and dst in self.nodes:
                self.connections_by_source[src][out_type].append((dst, in_idx))
                self.connections_by_dest[dst][in_type].append((src, out_idx))

    def get_start_nodes(self) -> list[str]:
        """找到入度为 0 的节点"""
        all_targets = set()
        for conns in self.connections_by_dest.values():
            for pairs in conns.values():
                # pairs 是 list[tuple[str, int]]，不需要 add 到 all_targets
                pass
        # 更直接：没有上游连接的节点
        nodes_with_input = set(self.connections_by_dest.keys())
        return [nid for nid in self.nodes if nid not in nodes_with_input]

    def get_downstream(self, node_id: str, output_type: str = ConnectionType.MAIN) -> list[tuple[str, int]]:
        """获取指定节点 + 输出类型的所有下游"""
        return self.connections_by_source.get(node_id, {}).get(output_type, [])

    def get_upstream_count(self, node_id: str, input_type: str = ConnectionType.MAIN) -> int:
        """获取指定节点的主输入上游数量"""
        return len(self.connections_by_dest.get(node_id, {}).get(input_type, []))

    def get_parent_nodes(self, node_id: str) -> list[str]:
        """获取所有上游节点"""
        parents = []
        for pairs in self.connections_by_dest.get(node_id, {}).values():
            for src, _ in pairs:
                if src not in parents:
                    parents.append(src)
        return parents


# ═══════ WorkflowEngine ═══════

# 事件类型
EVENT_NODE_STARTED = "node:started"
EVENT_NODE_FINISHED = "node:finished"
EVENT_NODE_ERROR = "node:error"
EVENT_NODE_SKIPPED = "node:skipped"
EVENT_WORKFLOW_STARTED = "workflow:started"
EVENT_WORKFLOW_FINISHED = "workflow:finished"

# 事件回调类型
EventCallback = Callable[[str, str, dict[str, Any]], None]


class WorkflowEngine:
    """n8n 式 BFS 工作流执行引擎。

    执行循环对标 WorkflowExecute.processRunExecutionData()。
    """

    def __init__(self):
        self._runs: dict[str, ExecutionContext] = {}

    def build(self, definition: WorkflowDefinition) -> Workflow:
        """构建运行时 Workflow"""
        return Workflow(definition)

    async def execute(
        self,
        workflow: Workflow,
        context: dict[str, Any],
        on_event: EventCallback | None = None,
    ) -> ExecutionContext:
        """BFS 执行工作流。

        Args:
            workflow: 运行时工作流
            context: 共享上下文（notebook_id、FileNotebook 等）
            on_event: 事件回调 (event_type, node_id, data)
        """
        run_id = f"run-{int(time.time() * 1000)}"
        exec_ctx = ExecutionContext(
            run_id=run_id,
            status=ExecutionStatus.RUNNING,
            started_at=time.time(),
            pin_data=workflow.pin_data,
        )
        self._runs[run_id] = exec_ctx

        # 发出 workflow:started
        _emit(on_event, EVENT_WORKFLOW_STARTED, "", {"run_id": run_id})

        # 初始化执行栈 — 入度为 0 的节点入栈
        start_nodes = workflow.get_start_nodes()
        for nid in start_nodes:
            if workflow.nodes[nid].disabled:
                continue
            exec_ctx.node_execution_stack.append(
                ExecuteData(
                    node_id=nid,
                    input_data={"main": [NodeExecutionData.empty()]},
                    source={"main": [None]},
                )
            )

        logger.info(f"[{run_id}] 起始节点: {start_nodes}")

        # ── BFS 主循环 ──
        while exec_ctx.node_execution_stack:
            execute_data = exec_ctx.node_execution_stack.pop(0)
            node_id = execute_data.node_id
            node = workflow.nodes.get(node_id)

            if not node:
                logger.warning(f"[{run_id}] 节点 {node_id} 不存在，跳过")
                continue

            if node.disabled:
                # 禁用节点 — 直接传递输入到下游
                _emit(on_event, EVENT_NODE_SKIPPED, node_id, {"reason": "disabled"})
                self._propagate_output(
                    workflow, exec_ctx, node_id,
                    {"main": [execute_data.get_main_input()]},
                    on_event,
                )
                continue

            # 检查 pinData — 跳过执行，直接使用固定数据
            if node_id in exec_ctx.pin_data:
                pin = exec_ctx.pin_data[node_id]
                pinned = NodeExecutionData(items=pin)
                node.status = NodeStatus.SUCCESS
                node.output_data = {"main": [pinned]}
                _emit(on_event, EVENT_NODE_FINISHED, node_id, {
                    "pinned": True,
                    "summary": pinned.summary(),
                    "elapsed_s": 0,
                })
                self._save_run_data(exec_ctx, node_id, node, 0)
                self._propagate_output(
                    workflow, exec_ctx, node_id,
                    {"main": [pinned]}, on_event,
                )
                continue

            # 执行节点
            node.status = NodeStatus.RUNNING
            _emit(on_event, EVENT_NODE_STARTED, node_id, {
                "type": node.type, "label": node.label,
            })

            t0 = time.time()
            step_fn = _STEP_REGISTRY.get(node.type)

            if not step_fn:
                node.status = NodeStatus.SKIPPED
                node.elapsed_s = round(time.time() - t0, 2)
                _emit(on_event, EVENT_NODE_SKIPPED, node_id, {
                    "reason": f"未注册类型: {node.type}",
                })
                # 跳过但仍传播空数据到下游
                self._propagate_output(
                    workflow, exec_ctx, node_id,
                    {"main": [NodeExecutionData.empty()]},
                    on_event,
                )
                continue

            try:
                input_data = execute_data.get_main_input()
                result = await step_fn(node, context, input_data)

                # 标准化输出
                if isinstance(result, NodeExecutionData):
                    output = {"main": [result]}
                elif isinstance(result, dict) and any(k in result for k in ("main", "error")):
                    output = result
                else:
                    # 兼容旧版返回 dict 的步骤
                    output = {"main": [NodeExecutionData.from_single(
                        result if isinstance(result, dict) else {"value": result}
                    )]}

                node.status = NodeStatus.SUCCESS
                node.output_data = output
                node.elapsed_s = round(time.time() - t0, 2)

                main_out = output.get("main", [NodeExecutionData.empty()])[0]
                summary = main_out.summary() if isinstance(main_out, NodeExecutionData) else "done"

                _emit(on_event, EVENT_NODE_FINISHED, node_id, {
                    "elapsed_s": node.elapsed_s,
                    "summary": summary,
                })

                self._save_run_data(exec_ctx, node_id, node, t0)
                self._propagate_output(workflow, exec_ctx, node_id, output, on_event)

                logger.info(f"[{run_id}] {node_id} 完成 ({node.elapsed_s}s)")

            except Exception as e:
                node.status = NodeStatus.ERROR
                node.error = str(e)
                node.elapsed_s = round(time.time() - t0, 2)

                _emit(on_event, EVENT_NODE_ERROR, node_id, {
                    "error": str(e),
                    "elapsed_s": node.elapsed_s,
                })

                logger.error(f"[{run_id}] {node_id} 失败: {e}")

                # 错误分支传播
                error_output = {"error": [NodeExecutionData.from_single({
                    "error": str(e), "node": node_id,
                })]}
                self._propagate_output(
                    workflow, exec_ctx, node_id, error_output, on_event,
                )

        # 完成
        exec_ctx.status = ExecutionStatus.SUCCESS
        exec_ctx.elapsed_s = round(time.time() - exec_ctx.started_at, 2)

        _emit(on_event, EVENT_WORKFLOW_FINISHED, "", {
            "run_id": run_id,
            "elapsed_s": exec_ctx.elapsed_s,
            "status": exec_ctx.status.value,
        })

        return exec_ctx

    def _propagate_output(
        self,
        workflow: Workflow,
        exec_ctx: ExecutionContext,
        node_id: str,
        output: dict[str, list[NodeExecutionData | None]],
        on_event: EventCallback | None,
    ):
        """将节点输出传播到下游 — 对标 n8n 的连线数据推送逻辑。

        如果下游所有输入就绪 → 入栈；否则放入 waiting_execution。
        """
        for output_type, output_list in output.items():
            downstream = workflow.get_downstream(node_id, output_type)

            for target_id, target_input_idx in downstream:
                target_node = workflow.nodes.get(target_id)
                if not target_node:
                    continue

                # 禁用节点 — 直接入栈，由主循环处理 passthrough
                if target_node.disabled:
                    exec_ctx.node_execution_stack.append(
                        ExecuteData(
                            node_id=target_id,
                            input_data={"main": output_list},
                            source={"main": [{"previousNode": node_id}]},
                        )
                    )
                    continue

                # 获取/初始化 waiting 数据
                if target_id not in exec_ctx.waiting_execution:
                    # 需要知道这个节点有多少上游
                    upstream_count = workflow.get_upstream_count(target_id, ConnectionType.MAIN)
                    exec_ctx.waiting_execution[target_id] = {
                        "main": [None] * max(upstream_count, 1),
                        "_needed": upstream_count,
                        "_received": 0,
                    }

                waiting = exec_ctx.waiting_execution[target_id]

                # 放入对应位置的数据
                if output_list and len(output_list) > 0:
                    idx = min(target_input_idx, len(waiting["main"]) - 1)
                    if idx >= 0:
                        waiting["main"][idx] = output_list[0] if output_list else None
                        waiting["_received"] = waiting.get("_received", 0) + 1

                # 检查是否所有输入就绪
                needed = waiting.get("_needed", 1)
                received = waiting.get("_received", 0)

                if received >= needed:
                    # 所有输入就绪 — 入栈
                    main_inputs = waiting.get("main", [])
                    exec_ctx.node_execution_stack.append(
                        ExecuteData(
                            node_id=target_id,
                            input_data={"main": main_inputs},
                            source={"main": [{"previousNode": node_id}]},
                        )
                    )
                    # 清理 waiting
                    del exec_ctx.waiting_execution[target_id]

    def _save_run_data(
        self,
        exec_ctx: ExecutionContext,
        node_id: str,
        node: WorkflowNode,
        start_time: float,
    ):
        """保存节点执行结果 — 对标 n8n runData"""
        if node_id not in exec_ctx.run_data:
            exec_ctx.run_data[node_id] = []

        exec_ctx.run_data[node_id].append({
            "status": node.status.value,
            "elapsed_s": node.elapsed_s,
            "error": node.error,
            "summary": self._summarize_output(node),
        })

    def _summarize_output(self, node: WorkflowNode) -> str | None:
        """节点输出摘要"""
        main = node.output_data.get("main", [])
        if main and main[0] and isinstance(main[0], NodeExecutionData):
            return main[0].summary()
        return None

    def get_run(self, run_id: str) -> ExecutionContext | None:
        return self._runs.get(run_id)

    def to_json(self, exec_ctx: ExecutionContext, workflow: Workflow) -> dict:
        """序列化执行结果"""
        return {
            "run_id": exec_ctx.run_id,
            "status": exec_ctx.status.value,
            "elapsed_s": exec_ctx.elapsed_s,
            "nodes": {
                nid: {
                    "id": nid,
                    "type": n.type,
                    "label": n.label,
                    "status": n.status.value,
                    "elapsed_s": n.elapsed_s,
                    "error": n.error,
                    "summary": self._summarize_output(n),
                }
                for nid, n in workflow.nodes.items()
            },
        }


def _emit(cb: EventCallback | None, event: str, node_id: str, data: dict):
    """安全触发事件"""
    if cb:
        try:
            cb(event, node_id, data)
        except Exception as e:
            logger.warning(f"事件回调异常: {e}")


# ═══════ 注册默认步骤（兼容现有 pipeline）═══════

@register_step("document_loader")
async def step_load(
    node: WorkflowNode, context: dict, input_data: NodeExecutionData
) -> NodeExecutionData:
    """文档加载"""
    fs = context.get("notebook")
    if fs:
        meta = fs.load_meta()
        return NodeExecutionData.from_single(meta)
    return NodeExecutionData.from_single({"status": "skipped", "reason": "已在上传阶段完成"})


@register_step("chunker")
async def step_chunk(
    node: WorkflowNode, context: dict, input_data: NodeExecutionData
) -> NodeExecutionData:
    """切分"""
    fs = context.get("notebook")
    if fs:
        chunks = fs.load_chunks()
        return NodeExecutionData.from_single({"chunks": chunks, "count": len(chunks)})
    return NodeExecutionData.from_single({"status": "skipped"})


@register_step("semantic_filter")
async def step_filter(
    node: WorkflowNode, context: dict, input_data: NodeExecutionData
) -> NodeExecutionData:
    """语义密度过滤"""
    return NodeExecutionData.from_single({"status": "done", "reason": "上传时自动完成"})


@register_step("schema_gen")
async def step_schema(
    node: WorkflowNode, context: dict, input_data: NodeExecutionData
) -> NodeExecutionData:
    """Schema 生成"""
    fs = context.get("notebook")
    prompt = node.parameters.get("system_prompt", "")
    if fs and prompt:
        meta = fs.load_meta()
        meta["system_prompt"] = prompt
        fs.save_meta(meta)
    return NodeExecutionData.from_single({"system_prompt_saved": bool(prompt)})


@register_step("extractor")
async def step_extract(
    node: WorkflowNode, context: dict, input_data: NodeExecutionData
) -> NodeExecutionData:
    """技能提取"""
    return NodeExecutionData.from_single({"status": "delegated", "reason": "通过 execute API 执行"})


@register_step("validator")
async def step_validate(
    node: WorkflowNode, context: dict, input_data: NodeExecutionData
) -> NodeExecutionData:
    """校验"""
    return NodeExecutionData.from_single({"status": "delegated", "reason": "通过 sample-check API 执行"})


@register_step("reducer")
async def step_reduce(
    node: WorkflowNode, context: dict, input_data: NodeExecutionData
) -> NodeExecutionData:
    """聚类去重"""
    return NodeExecutionData.from_single({"status": "delegated", "reason": "在全量执行中自动触发"})


@register_step("classifier")
async def step_classify(
    node: WorkflowNode, context: dict, input_data: NodeExecutionData
) -> NodeExecutionData:
    """SKU 分类"""
    return NodeExecutionData.from_single({"status": "delegated", "reason": "在全量执行中自动触发"})


@register_step("packager")
async def step_package(
    node: WorkflowNode, context: dict, input_data: NodeExecutionData
) -> NodeExecutionData:
    """打包输出"""
    return NodeExecutionData.from_single({"status": "delegated", "reason": "在全量执行中自动触发"})
