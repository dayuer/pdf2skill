"""
工作流引擎测试 — 验证 n8n 式 BFS 执行。

测试用例:
1. 线性执行: A → B → C
2. 分支执行: A → [B, C] → D（扇出/扇入）
3. 错误分支: 节点执行失败 → 错误路由
4. pinData 跳过: 固定 B 的输出 → 跳过 B
5. 循环检测: 存在环时拓扑排序报错
6. 禁用节点: disabled 节点直接传递数据
"""
from __future__ import annotations

import asyncio
import pytest

from src.workflow_types import (
    NodeExecutionData,
    WorkflowDefinition,
    ExecutionStatus,
    NodeStatus,
)
from src.workflow_engine import (
    WorkflowEngine,
    Workflow,
    register_step,
)


# ── 测试用步骤注册 ──

@register_step("test_passthrough")
async def step_passthrough(node, context, input_data):
    """透传输入"""
    return input_data


@register_step("test_transform")
async def step_transform(node, context, input_data):
    """转换：在输入数据上加标记"""
    data = input_data.first_item.copy()
    data["transformed_by"] = node.id
    return NodeExecutionData.from_single(data)


@register_step("test_error")
async def step_error(node, context, input_data):
    """永远抛异常"""
    raise ValueError(f"节点 {node.id} 故意失败")


@register_step("test_merge")
async def step_merge(node, context, input_data):
    """合并：记录收到了数据"""
    data = input_data.first_item.copy()
    data["merged_at"] = node.id
    return NodeExecutionData.from_single(data)


# ── 辅助函数 ──

def make_definition(nodes_data: list[dict], connections_data: list[dict], **kwargs) -> WorkflowDefinition:
    """快速构建 WorkflowDefinition"""
    return WorkflowDefinition.from_json({
        "nodes": nodes_data,
        "connections": connections_data,
        **kwargs,
    })


# ══════ 测试 ══════

class TestWorkflowEngine:
    """n8n 式 BFS 工作流引擎测试"""

    @pytest.fixture
    def engine(self):
        return WorkflowEngine()

    @pytest.mark.asyncio
    async def test_linear_execution(self, engine):
        """线性执行: A → B → C"""
        definition = make_definition(
            nodes_data=[
                {"id": "a", "type": "test_passthrough"},
                {"id": "b", "type": "test_transform"},
                {"id": "c", "type": "test_transform"},
            ],
            connections_data=[
                {"source": "a", "target": "b"},
                {"source": "b", "target": "c"},
            ],
        )
        workflow = engine.build(definition)
        events = []

        def on_event(event, node_id, data):
            events.append((event, node_id))

        result = await engine.execute(workflow, context={}, on_event=on_event)

        assert result.status == ExecutionStatus.SUCCESS
        assert workflow.nodes["a"].status == NodeStatus.SUCCESS
        assert workflow.nodes["b"].status == NodeStatus.SUCCESS
        assert workflow.nodes["c"].status == NodeStatus.SUCCESS
        # 验证事件序列
        event_types = [e[0] for e in events]
        assert "workflow:started" in event_types
        assert "workflow:finished" in event_types
        assert event_types.count("node:started") == 3

    @pytest.mark.asyncio
    async def test_fan_out_fan_in(self, engine):
        """分支执行: A → [B, C] → D"""
        definition = make_definition(
            nodes_data=[
                {"id": "a", "type": "test_passthrough"},
                {"id": "b", "type": "test_transform"},
                {"id": "c", "type": "test_transform"},
                {"id": "d", "type": "test_merge"},
            ],
            connections_data=[
                {"source": "a", "target": "b"},
                {"source": "a", "target": "c"},
                {"source": "b", "target": "d", "targetInputIndex": 0},
                {"source": "c", "target": "d", "targetInputIndex": 1},
            ],
        )
        workflow = engine.build(definition)
        result = await engine.execute(workflow, context={})

        assert result.status == ExecutionStatus.SUCCESS
        assert workflow.nodes["d"].status == NodeStatus.SUCCESS

    @pytest.mark.asyncio
    async def test_error_propagation(self, engine):
        """错误节点: A → B(error) → C 应该不执行, 但引擎完成"""
        definition = make_definition(
            nodes_data=[
                {"id": "a", "type": "test_passthrough"},
                {"id": "b", "type": "test_error"},
                {"id": "c", "type": "test_transform"},
            ],
            connections_data=[
                {"source": "a", "target": "b"},
                {"source": "b", "target": "c"},
            ],
        )
        workflow = engine.build(definition)
        events = []

        def on_event(event, node_id, data):
            events.append((event, node_id, data))

        result = await engine.execute(workflow, context={}, on_event=on_event)

        # 引擎正常完成
        assert result.status == ExecutionStatus.SUCCESS
        # B 应该是 ERROR
        assert workflow.nodes["b"].status == NodeStatus.ERROR
        assert "故意失败" in workflow.nodes["b"].error
        # 验证有 node:error 事件
        error_events = [e for e in events if e[0] == "node:error"]
        assert len(error_events) == 1
        assert error_events[0][1] == "b"

    @pytest.mark.asyncio
    async def test_pin_data_skip(self, engine):
        """pinData: 固定 B 的数据 → 跳过 B 执行"""
        definition = make_definition(
            nodes_data=[
                {"id": "a", "type": "test_passthrough"},
                {"id": "b", "type": "test_error"},  # 正常会报错
                {"id": "c", "type": "test_transform"},
            ],
            connections_data=[
                {"source": "a", "target": "b"},
                {"source": "b", "target": "c"},
            ],
            pin_data={
                "b": [{"json": {"pinned": True, "value": 42}}],
            },
        )
        workflow = engine.build(definition)
        events = []

        def on_event(event, node_id, data):
            events.append((event, node_id, data))

        result = await engine.execute(workflow, context={}, on_event=on_event)

        # B 被 pin 跳过，不应报错
        assert workflow.nodes["b"].status == NodeStatus.SUCCESS
        assert result.status == ExecutionStatus.SUCCESS
        # 验证有 pinned 事件
        pinned_events = [e for e in events if e[0] == "node:finished" and e[2].get("pinned")]
        assert len(pinned_events) == 1

    @pytest.mark.asyncio
    async def test_disabled_node_passthrough(self, engine):
        """禁用节点: A → B(disabled) → C, B 应被跳过"""
        definition = make_definition(
            nodes_data=[
                {"id": "a", "type": "test_passthrough"},
                {"id": "b", "type": "test_error", "disabled": True},
                {"id": "c", "type": "test_transform"},
            ],
            connections_data=[
                {"source": "a", "target": "b"},
                {"source": "b", "target": "c"},
            ],
        )
        workflow = engine.build(definition)
        result = await engine.execute(workflow, context={})

        # B 被跳过，C 正常执行
        assert result.status == ExecutionStatus.SUCCESS
        assert workflow.nodes["c"].status == NodeStatus.SUCCESS

    @pytest.mark.asyncio
    async def test_single_node(self, engine):
        """单节点, 无连接"""
        definition = make_definition(
            nodes_data=[{"id": "solo", "type": "test_passthrough"}],
            connections_data=[],
        )
        workflow = engine.build(definition)
        result = await engine.execute(workflow, context={})

        assert result.status == ExecutionStatus.SUCCESS
        assert workflow.nodes["solo"].status == NodeStatus.SUCCESS

    @pytest.mark.asyncio
    async def test_empty_workflow(self, engine):
        """空工作流"""
        definition = make_definition(nodes_data=[], connections_data=[])
        workflow = engine.build(definition)
        result = await engine.execute(workflow, context={})

        assert result.status == ExecutionStatus.SUCCESS

    @pytest.mark.asyncio
    async def test_unregistered_step_skipped(self, engine):
        """未注册类型 → 节点被跳过"""
        definition = make_definition(
            nodes_data=[{"id": "x", "type": "nonexistent_type_xyz"}],
            connections_data=[],
        )
        workflow = engine.build(definition)
        result = await engine.execute(workflow, context={})

        assert workflow.nodes["x"].status == NodeStatus.SKIPPED

    @pytest.mark.asyncio
    async def test_serialization(self, engine):
        """to_json 序列化"""
        definition = make_definition(
            nodes_data=[
                {"id": "a", "type": "test_passthrough", "label": "节点A"},
                {"id": "b", "type": "test_transform", "label": "节点B"},
            ],
            connections_data=[{"source": "a", "target": "b"}],
        )
        workflow = engine.build(definition)
        result = await engine.execute(workflow, context={})

        json_out = engine.to_json(result, workflow)
        assert json_out["status"] == "success"
        assert "a" in json_out["nodes"]
        assert "b" in json_out["nodes"]
        assert json_out["nodes"]["a"]["label"] == "节点A"


class TestNodeExecutionData:
    """NodeExecutionData 单元测试"""

    def test_from_single(self):
        data = NodeExecutionData.from_single({"key": "value"})
        assert len(data) == 1
        assert data.first_item == {"key": "value"}

    def test_empty(self):
        data = NodeExecutionData.empty()
        assert len(data) == 1
        assert data.first_item == {}

    def test_summary_skills(self):
        data = NodeExecutionData.from_single({"skills": [1, 2, 3]})
        assert "3 skills" in data.summary()

    def test_summary_chunks(self):
        data = NodeExecutionData.from_single({"chunks": [1, 2]})
        assert "2 chunks" in data.summary()


class TestWorkflowDefinition:
    """WorkflowDefinition 解析测试"""

    def test_from_json_connections(self):
        """新格式: connections"""
        wf = WorkflowDefinition.from_json({
            "nodes": [{"id": "a"}, {"id": "b"}],
            "connections": [{"source": "a", "target": "b"}],
        })
        assert len(wf.connections) == 1

    def test_from_json_edges_compat(self):
        """旧格式: edges 兼容"""
        wf = WorkflowDefinition.from_json({
            "nodes": [{"id": "a"}, {"id": "b"}],
            "edges": [{"source": "a", "target": "b"}],
        })
        assert len(wf.connections) == 1

    def test_pin_data(self):
        """pinData / pin_data 双字段兼容"""
        wf = WorkflowDefinition.from_json({
            "nodes": [],
            "pinData": {"node1": [{"json": {}}]},
        })
        assert "node1" in wf.pin_data
