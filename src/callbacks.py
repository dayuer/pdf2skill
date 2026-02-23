"""
管线事件回调系统 — 解耦 Pipeline 执行与状态展示。

将 Pipeline 的每一步（分块、提取、校验、合并）通过事件回调推送，
支持多消费者（SSE、日志、Metrics）同时监听。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Coroutine

logger = logging.getLogger(__name__)


class EventType(str, Enum):
    """管线事件类型枚举"""

    # 阶段级
    PHASE_START = "phase_start"
    PHASE_END = "phase_end"

    # 进度级
    CHUNK_PROGRESS = "chunk_progress"
    BATCH_COMPLETE = "batch_complete"

    # Skill 级
    SKILL_EXTRACTED = "skill_extracted"
    SKILL_VALIDATED = "skill_validated"
    SKILL_MERGED = "skill_merged"

    # 系统级
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass
class PipelineEvent:
    """管线事件数据"""

    event_type: EventType
    data: dict[str, Any]
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


# 回调函数签名：接收 EventType 和 data 字典
CallbackFn = Callable[[EventType, dict[str, Any]], Coroutine[Any, Any, None]]


class StatusCallback:
    """
    管线事件分发器 — 支持注册多个异步回调。

    用法：
        callback = StatusCallback()
        callback.add_callback(my_sse_handler)
        callback.add_callback(my_log_handler)

        await callback.emit(EventType.PHASE_START, {"phase": "extraction"})
    """

    def __init__(self) -> None:
        self._callbacks: list[CallbackFn] = []

    def add_callback(self, fn: CallbackFn) -> None:
        """注册一个异步回调函数"""
        self._callbacks.append(fn)

    def remove_callback(self, fn: CallbackFn) -> None:
        """移除一个回调函数"""
        self._callbacks = [cb for cb in self._callbacks if cb is not fn]

    async def emit(self, event_type: EventType, data: dict[str, Any]) -> None:
        """
        分发事件到所有注册的回调。

        单个回调异常不会影响其他回调的执行。
        """
        for cb in self._callbacks:
            try:
                await cb(event_type, data)
            except Exception as e:
                logger.error(f"回调执行失败 [{event_type.value}]: {e}")

    async def emit_phase_start(self, phase: str, **kwargs: Any) -> None:
        """便捷方法：阶段开始"""
        await self.emit(EventType.PHASE_START, {"phase": phase, **kwargs})

    async def emit_phase_end(self, phase: str, **kwargs: Any) -> None:
        """便捷方法：阶段结束"""
        await self.emit(EventType.PHASE_END, {"phase": phase, **kwargs})

    async def emit_progress(
        self,
        completed: int,
        total: int,
        **kwargs: Any,
    ) -> None:
        """便捷方法：进度更新"""
        await self.emit(
            EventType.CHUNK_PROGRESS,
            {"completed": completed, "total": total, **kwargs},
        )

    async def emit_error(self, message: str, **kwargs: Any) -> None:
        """便捷方法：错误事件"""
        await self.emit(EventType.ERROR, {"message": message, **kwargs})


def create_logging_callback(name: str = "pipeline") -> CallbackFn:
    """
    创建一个日志回调 — 将所有事件写入 logger。

    适用于调试和审计。
    """
    _logger = logging.getLogger(name)

    async def _log_callback(event_type: EventType, data: dict[str, Any]) -> None:
        _logger.info(f"[{event_type.value}] {data}")

    return _log_callback
