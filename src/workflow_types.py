"""
工作流类型系统 — 对标 n8n packages/workflow/src/interfaces.ts

核心设计：
- Connection 使用嵌套字典，天然支持多输出/条件分支
- NodeExecutionData 是节点间数据传递的标准容器
- ExecutionContext 管理执行栈和等待队列
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ── 枚举 ──

class NodeStatus(str, Enum):
    """节点执行状态"""
    IDLE = "idle"
    RUNNING = "running"
    SUCCESS = "success"
    ERROR = "error"
    SKIPPED = "skipped"
    WAITING = "waiting"


class ExecutionStatus(str, Enum):
    """工作流执行状态"""
    NEW = "new"
    RUNNING = "running"
    SUCCESS = "success"
    ERROR = "error"
    CANCELLED = "cancelled"
    WAITING = "waiting"


class ConnectionType(str, Enum):
    """连接类型 — 对标 n8n NodeConnectionTypes"""
    MAIN = "main"        # 正常数据流
    ERROR = "error"      # 错误分支
    AI_TOOL = "ai_tool"  # AI 工具链


# ── 核心数据类 ──

@dataclass
class NodeExecutionData:
    """
    节点输入/输出数据容器 — 对标 n8n INodeExecutionData。

    每个 item 是一个 dict，包含 json 数据和可选的元信息。
    """
    items: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_single(cls, data: dict[str, Any]) -> NodeExecutionData:
        return cls(items=[{"json": data}])

    @classmethod
    def empty(cls) -> NodeExecutionData:
        return cls(items=[{"json": {}}])

    @property
    def first_item(self) -> dict[str, Any]:
        if self.items:
            return self.items[0].get("json", {})
        return {}

    def to_dict(self) -> list[dict]:
        return self.items

    def __len__(self) -> int:
        return len(self.items)

    def summary(self) -> str:
        """输出摘要"""
        if not self.items:
            return "empty"
        n = len(self.items)
        first = self.items[0].get("json", {})
        if "skills" in first:
            return f"{len(first['skills'])} skills"
        if "chunks" in first:
            return f"{len(first['chunks'])} chunks"
        return f"{n} items"


@dataclass
class Connection:
    """
    单条连接 — 对标 n8n IConnection。

    n8n 的连接结构：connections[sourceNode][outputType][outputIndex] = [{node, type, index}]
    我们简化为扁平对象 + 按需构建嵌套索引。
    """
    source: str
    source_output_type: ConnectionType = ConnectionType.MAIN
    source_output_index: int = 0
    target: str = ""
    target_input_type: ConnectionType = ConnectionType.MAIN
    target_input_index: int = 0


@dataclass
class WorkflowNode:
    """
    工作流节点定义 — 对标 n8n INode。

    与 n8n 的差异：
    - 无 typeVersion（我们的节点类型更简单）
    - 增加 label/icon/desc 用于 UI 展示
    """
    id: str
    type: str
    label: str = ""
    icon: str = ""
    desc: str = ""
    position: tuple[float, float] = (0.0, 0.0)
    parameters: dict[str, Any] = field(default_factory=dict)
    disabled: bool = False
    # 运行时状态（非持久化）
    status: NodeStatus = NodeStatus.IDLE
    output_data: dict[str, list[NodeExecutionData | None]] = field(default_factory=dict)
    error: str | None = None
    elapsed_s: float = 0.0


@dataclass
class WorkflowDefinition:
    """
    完整工作流定义 — 对标 n8n Workflow 构造参数。

    持久化到 workflow.json 的结构。
    """
    id: str = ""
    name: str = ""
    nodes: list[dict[str, Any]] = field(default_factory=list)
    connections: list[dict[str, Any]] = field(default_factory=list)
    settings: dict[str, Any] = field(default_factory=dict)
    pin_data: dict[str, list[dict]] = field(default_factory=dict)

    @classmethod
    def from_json(cls, data: dict) -> WorkflowDefinition:
        """从 JSON 解析（兼容旧格式 edges）"""
        connections = data.get("connections", [])
        # 兼容旧的 edges 格式
        if not connections and "edges" in data:
            connections = [
                {"source": e["source"], "target": e["target"]}
                for e in data["edges"]
            ]
        return cls(
            id=data.get("id", f"wf-{int(time.time() * 1000)}"),
            name=data.get("name", ""),
            nodes=data.get("nodes", []),
            connections=connections,
            settings=data.get("settings", {}),
            pin_data=data.get("pin_data", data.get("pinData", {})),
        )


# ── 执行上下文 ──

@dataclass
class ExecuteData:
    """
    执行栈中的一项 — 对标 n8n IExecuteData。

    包含待执行的节点 + 输入数据 + 来源信息。
    """
    node_id: str
    input_data: dict[str, list[NodeExecutionData | None]] = field(default_factory=dict)
    source: dict[str, list[dict | None]] = field(default_factory=dict)

    def get_main_input(self) -> NodeExecutionData:
        """获取主输入数据"""
        main = self.input_data.get("main", [])
        if main and main[0]:
            return main[0]
        return NodeExecutionData.empty()


@dataclass
class ExecutionContext:
    """
    执行状态 — 对标 n8n IRunExecutionData。

    管理 nodeExecutionStack 和 waitingExecution。
    """
    run_id: str = ""
    status: ExecutionStatus = ExecutionStatus.NEW

    # BFS 执行栈
    node_execution_stack: list[ExecuteData] = field(default_factory=list)

    # 等待中的节点（等待所有上游数据就绪）
    waiting_execution: dict[str, dict[str, list[NodeExecutionData | None]]] = field(
        default_factory=dict
    )

    # 结果
    run_data: dict[str, list[dict]] = field(default_factory=dict)

    # 时间
    started_at: float = 0.0
    elapsed_s: float = 0.0

    # pinData
    pin_data: dict[str, list[dict]] = field(default_factory=dict)
