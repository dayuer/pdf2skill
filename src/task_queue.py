"""统一任务队列 — Redis 可用时用 Redis，否则 fallback asyncio.Queue。

用法:
    from .task_queue import task_queue

    # 入队
    await task_queue.enqueue("chunk", {"workflow_id": "...", "filename": "..."})

    # 更新进度（SSE 读取）
    task_queue.set_progress("chunk:wf:fn", {"status": "chunking", ...})
    progress = task_queue.get_progress("chunk:wf:fn")

    # 注册 worker（启动时调用一次）
    task_queue.register_worker("chunk", my_worker_fn)
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable, Coroutine

logger = logging.getLogger(__name__)

# Redis 进度 key 前缀
_REDIS_PROGRESS_PREFIX = "pdf2skill:progress:"
_REDIS_QUEUE_PREFIX = "pdf2skill:queue:"
_PROGRESS_TTL = 3600  # 1 小时后自动清理


class _RedisBackend:
    """Redis 后端 — 持久化队列 + 进度。"""

    def __init__(self, redis_url: str):
        import redis.asyncio as aioredis
        self._redis = aioredis.from_url(redis_url, decode_responses=True)
        self._workers: dict[str, Callable] = {}
        self._consumer_tasks: dict[str, asyncio.Task] = {}

    async def ping(self) -> bool:
        try:
            return await self._redis.ping()
        except Exception:
            return False

    async def enqueue(self, queue_name: str, payload: dict) -> None:
        key = f"{_REDIS_QUEUE_PREFIX}{queue_name}"
        await self._redis.lpush(key, json.dumps(payload, ensure_ascii=False))
        self._ensure_consumer(queue_name)

    def set_progress(self, task_key: str, data: dict) -> None:
        """同步写进度（在 async 上下文中用 fire-and-forget）。"""
        asyncio.ensure_future(self._async_set_progress(task_key, data))

    async def _async_set_progress(self, task_key: str, data: dict) -> None:
        key = f"{_REDIS_PROGRESS_PREFIX}{task_key}"
        await self._redis.set(key, json.dumps(data, ensure_ascii=False), ex=_PROGRESS_TTL)

    async def get_progress(self, task_key: str) -> dict:
        key = f"{_REDIS_PROGRESS_PREFIX}{task_key}"
        raw = await self._redis.get(key)
        if raw:
            return json.loads(raw)
        return {"status": "unknown", "message": "无任务记录"}

    def register_worker(self, queue_name: str, handler: Callable) -> None:
        self._workers[queue_name] = handler

    def _ensure_consumer(self, queue_name: str) -> None:
        task = self._consumer_tasks.get(queue_name)
        if task is None or task.done():
            self._consumer_tasks[queue_name] = asyncio.create_task(
                self._consumer_loop(queue_name)
            )

    async def _consumer_loop(self, queue_name: str) -> None:
        key = f"{_REDIS_QUEUE_PREFIX}{queue_name}"
        handler = self._workers.get(queue_name)
        if not handler:
            logger.error("队列 %s 无注册 worker", queue_name)
            return
        logger.info("Redis 消费者启动: %s", queue_name)
        while True:
            try:
                # BRPOP: 阻塞式右取，超时 60s
                result = await self._redis.brpop(key, timeout=60)
                if result is None:
                    # 60s 无任务，退出（下次入队自动重启）
                    break
                _, raw = result
                payload = json.loads(raw)
                await handler(payload)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception("队列 %s 消费异常: %s", queue_name, e)
                await asyncio.sleep(1)
        logger.info("Redis 消费者退出: %s", queue_name)

    async def close(self) -> None:
        await self._redis.close()


class _MemoryBackend:
    """内存后端 — asyncio.Queue fallback（开发环境）。"""

    def __init__(self):
        self._queues: dict[str, asyncio.Queue] = {}
        self._progress: dict[str, dict] = {}
        self._workers: dict[str, Callable] = {}
        self._consumer_tasks: dict[str, asyncio.Task] = {}

    async def ping(self) -> bool:
        return True

    async def enqueue(self, queue_name: str, payload: dict) -> None:
        if queue_name not in self._queues:
            self._queues[queue_name] = asyncio.Queue()
        await self._queues[queue_name].put(payload)
        self._ensure_consumer(queue_name)

    def set_progress(self, task_key: str, data: dict) -> None:
        self._progress[task_key] = data

    async def get_progress(self, task_key: str) -> dict:
        return self._progress.get(task_key, {"status": "unknown", "message": "无任务记录"})

    def register_worker(self, queue_name: str, handler: Callable) -> None:
        self._workers[queue_name] = handler

    def _ensure_consumer(self, queue_name: str) -> None:
        task = self._consumer_tasks.get(queue_name)
        if task is None or task.done():
            self._consumer_tasks[queue_name] = asyncio.create_task(
                self._consumer_loop(queue_name)
            )

    async def _consumer_loop(self, queue_name: str) -> None:
        queue = self._queues.get(queue_name)
        handler = self._workers.get(queue_name)
        if not queue or not handler:
            return
        logger.info("Memory 消费者启动: %s", queue_name)
        while True:
            try:
                payload = await asyncio.wait_for(queue.get(), timeout=60)
                await handler(payload)
                queue.task_done()
            except asyncio.TimeoutError:
                break
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception("队列 %s 消费异常: %s", queue_name, e)
        logger.info("Memory 消费者退出: %s", queue_name)

    async def close(self) -> None:
        pass


class TaskQueue:
    """统一任务队列门面 — 自动选择 Redis 或 Memory 后端。"""

    def __init__(self):
        self._backend: _RedisBackend | _MemoryBackend | None = None
        self._redis_url: str = ""

    async def init(self, redis_url: str = "") -> str:
        """初始化后端。返回 'redis' 或 'memory'。"""
        self._redis_url = redis_url
        if redis_url:
            try:
                backend = _RedisBackend(redis_url)
                if await backend.ping():
                    self._backend = backend
                    logger.info("✅ 任务队列: Redis (%s)", redis_url)
                    return "redis"
                else:
                    await backend.close()
            except Exception as e:
                logger.warning("Redis 不可用 (%s): %s，降级为 Memory", redis_url, e)

        self._backend = _MemoryBackend()
        logger.info("✅ 任务队列: Memory (asyncio.Queue)")
        return "memory"

    @property
    def backend_type(self) -> str:
        if isinstance(self._backend, _RedisBackend):
            return "redis"
        return "memory"

    async def enqueue(self, queue_name: str, payload: dict) -> None:
        assert self._backend, "TaskQueue 未初始化，先调用 init()"
        await self._backend.enqueue(queue_name, payload)

    def set_progress(self, task_key: str, data: dict) -> None:
        assert self._backend, "TaskQueue 未初始化"
        self._backend.set_progress(task_key, data)

    async def get_progress(self, task_key: str) -> dict:
        assert self._backend, "TaskQueue 未初始化"
        return await self._backend.get_progress(task_key)

    def register_worker(self, queue_name: str, handler: Callable) -> None:
        if self._backend:
            self._backend.register_worker(queue_name, handler)

    async def close(self) -> None:
        if self._backend:
            await self._backend.close()


# 全局单例
task_queue = TaskQueue()
