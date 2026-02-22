"""
DeepSeek R1 客户端 — 封装 API 调用、重试、<think> 丢弃。

核心特性：
1. 使用 OpenAI SDK 兼容接口调用 DeepSeek R1
2. 自动丢弃 <think>...</think> 思考过程，仅返回最终结论
3. 指数退避重试机制
4. 并发控制（asyncio.Semaphore）
5. 调用日志自动记录（JSONL）
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from openai import AsyncOpenAI, OpenAI

from .config import config


# 匹配 <think>...</think> 标签（含换行）
_THINK_TAG_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


@dataclass
class LLMResponse:
    """大模型调用结果"""

    content: str           # 最终输出（已去除 <think>）
    raw_content: str       # 原始输出（含 <think>）
    think_content: str     # 思考过程
    input_tokens: int = 0
    output_tokens: int = 0
    think_tokens: int = 0  # 思考过程占用的 Token 估算
    latency_ms: int = 0
    model: str = ""


def _strip_think(text: str) -> tuple[str, str]:
    """
    分离 <think> 思考过程和最终输出。

    Returns:
        (最终输出, 思考过程)
    """
    think_parts: list[str] = []
    for m in _THINK_TAG_RE.finditer(text):
        # 提取 <think>...</think> 内的内容
        inner = m.group(0)[7:-8]  # 去掉 <think> 和 </think>
        think_parts.append(inner.strip())

    # 移除所有 <think> 块
    clean = _THINK_TAG_RE.sub("", text).strip()

    return clean, "\n\n".join(think_parts)


# ──── 调用日志 ────


@dataclass
class CallLog:
    """单次 R1 调用的日志记录"""

    run_id: str
    phase: str
    prompt_version: str
    system_prompt: str
    user_prompt: str
    input_metadata: dict
    output_raw: str
    output_clean: str
    think_content: str
    input_tokens: int
    output_tokens: int
    think_tokens: int
    latency_ms: int
    model: str
    validation_result: str
    human_label: Optional[str]
    timestamp: str


class CallLogger:
    """JSONL 格式的调用日志管理器"""

    def __init__(self, log_dir: str | Path | None = None) -> None:
        self.log_dir = Path(log_dir or config.log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def log(
        self,
        *,
        phase: str,
        prompt_version: str,
        system_prompt: str,
        user_prompt: str,
        response: LLMResponse,
        input_metadata: dict | None = None,
        validation_result: str = "pending",
    ) -> str:
        """记录一条调用日志，返回 run_id"""
        run_id = str(uuid.uuid4())[:8]
        record = CallLog(
            run_id=run_id,
            phase=phase,
            prompt_version=prompt_version,
            system_prompt=system_prompt[:500],  # 截断避免日志过大
            user_prompt=user_prompt[:500],
            input_metadata=input_metadata or {},
            output_raw=response.raw_content[:2000],
            output_clean=response.content[:2000],
            think_content=response.think_content[:1000],
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            think_tokens=response.think_tokens,
            latency_ms=response.latency_ms,
            model=response.model,
            validation_result=validation_result,
            human_label=None,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

        # 按日期分文件
        date_str = datetime.now().strftime("%Y-%m-%d")
        log_file = self.log_dir / f"{date_str}.jsonl"

        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record.__dict__, ensure_ascii=False) + "\n")

        return run_id


# ──── 同步客户端 ────


class DeepSeekClient:
    """DeepSeek R1 同步调用客户端"""

    def __init__(self, cfg: Optional[object] = None) -> None:
        self.cfg = cfg or config.llm
        self.client = OpenAI(
            base_url=self.cfg.base_url,
            api_key=self.cfg.api_key,
            timeout=self.cfg.timeout,
        )
        self.logger = CallLogger()

    def chat(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> LLMResponse:
        """
        发起一次同步聊天请求。

        自动重试、自动丢弃 <think> 标签。
        """
        temp = temperature if temperature is not None else self.cfg.temperature
        max_tok = max_tokens or self.cfg.max_tokens

        last_err: Exception | None = None

        for attempt in range(config.max_retries + 1):
            try:
                t0 = time.monotonic()

                resp = self.client.chat.completions.create(
                    model=self.cfg.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=temp,
                    max_tokens=max_tok,
                )

                latency = int((time.monotonic() - t0) * 1000)
                raw = resp.choices[0].message.content or ""
                clean, think = _strip_think(raw)

                usage = resp.usage
                result = LLMResponse(
                    content=clean,
                    raw_content=raw,
                    think_content=think,
                    input_tokens=usage.prompt_tokens if usage else 0,
                    output_tokens=usage.completion_tokens if usage else 0,
                    think_tokens=len(think) // 2,  # 粗略估算
                    latency_ms=latency,
                    model=self.cfg.model,
                )
                return result

            except Exception as e:
                last_err = e
                if attempt < config.max_retries:
                    delay = min(
                        config.retry_base_delay * (2 ** attempt),
                        config.retry_max_delay,
                    )
                    time.sleep(delay)

        raise RuntimeError(
            f"DeepSeek R1 调用失败（重试 {config.max_retries} 次后）：{last_err}"
        )


# ──── 异步客户端 ────


class AsyncDeepSeekClient:
    """DeepSeek R1 异步调用客户端，支持并发控制"""

    def __init__(self, cfg: Optional[object] = None) -> None:
        self.cfg = cfg or config.llm
        self.client = AsyncOpenAI(
            base_url=self.cfg.base_url,
            api_key=self.cfg.api_key,
            timeout=self.cfg.timeout,
        )
        self.logger = CallLogger()
        self._semaphore = asyncio.Semaphore(config.max_concurrent_requests)

    async def chat(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> LLMResponse:
        """发起一次异步聊天请求，带并发控制和重试。"""
        async with self._semaphore:
            return await self._chat_with_retry(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=temperature,
                max_tokens=max_tokens,
            )

    async def _chat_with_retry(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> LLMResponse:
        temp = temperature if temperature is not None else self.cfg.temperature
        max_tok = max_tokens or self.cfg.max_tokens

        last_err: Exception | None = None

        for attempt in range(config.max_retries + 1):
            try:
                t0 = time.monotonic()

                resp = await self.client.chat.completions.create(
                    model=self.cfg.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=temp,
                    max_tokens=max_tok,
                )

                latency = int((time.monotonic() - t0) * 1000)
                raw = resp.choices[0].message.content or ""
                clean, think = _strip_think(raw)

                usage = resp.usage
                result = LLMResponse(
                    content=clean,
                    raw_content=raw,
                    think_content=think,
                    input_tokens=usage.prompt_tokens if usage else 0,
                    output_tokens=usage.completion_tokens if usage else 0,
                    think_tokens=len(think) // 2,
                    latency_ms=latency,
                    model=self.cfg.model,
                )
                return result

            except Exception as e:
                last_err = e
                if attempt < config.max_retries:
                    delay = min(
                        config.retry_base_delay * (2 ** attempt),
                        config.retry_max_delay,
                    )
                    await asyncio.sleep(delay)

        raise RuntimeError(
            f"DeepSeek R1 异步调用失败（重试 {config.max_retries} 次后）：{last_err}"
        )

    async def batch_chat(
        self,
        tasks: list[dict],
        *,
        phase: str = "extraction",
        prompt_version: str = "v0.1",
    ) -> list[LLMResponse]:
        """
        批量并发调用，自动控制并发数。带实时进度条。

        Args:
            tasks: [{"system_prompt": ..., "user_prompt": ..., "metadata": ...}]
            phase: 日志阶段标识
            prompt_version: Prompt 版本号

        Returns:
            与 tasks 等长的 LLMResponse 列表
        """
        import sys
        total = len(tasks)
        _EMPTY = LLMResponse(
            content="EMPTY_BLOCK", raw_content="EMPTY_BLOCK",
            think_content="", model=self.cfg.model,
        )
        results: list[LLMResponse] = [_EMPTY] * total
        _done = 0
        _ok = 0
        _fail = 0
        _t0 = time.monotonic()
        _lats: list[float] = []

        def _bar() -> None:
            e = time.monotonic() - _t0
            p = _done / total * 100 if total else 100
            if _lats:
                avg = sum(_lats[-30:]) / len(_lats[-30:])
                rem = total - _done
                c = min(config.max_concurrent_requests, max(rem, 1))
                eta = (rem / c) * avg
                es = f"ETA {eta/60:.0f}m" if eta > 60 else f"ETA {eta:.0f}s"
            else:
                es = "ETA ..."
            n = 30
            f = int(n * _done / total) if total else n
            sys.stdout.write(
                f"\r  {'█'*f}{'░'*(n-f)} {_done}/{total} "
                f"({p:.0f}%) ✅{_ok} ❌{_fail} ⏱{e:.0f}s {es}   "
            )
            sys.stdout.flush()

        async def _one(idx: int, task: dict) -> None:
            nonlocal _done, _ok, _fail
            t = time.monotonic()
            try:
                resp = await self.chat(
                    system_prompt=task["system_prompt"],
                    user_prompt=task["user_prompt"],
                )
                self.logger.log(
                    phase=phase, prompt_version=prompt_version,
                    system_prompt=task["system_prompt"],
                    user_prompt=task["user_prompt"],
                    response=resp, input_metadata=task.get("metadata"),
                )
                results[idx] = resp
                _ok += 1
            except Exception:
                _fail += 1
            finally:
                _lats.append(time.monotonic() - t)
                _done += 1
                _bar()

        _bar()
        await asyncio.gather(*[_one(i, t) for i, t in enumerate(tasks)])
        print()
        if _fail:
            print(f"  ⚠️ {_fail}/{total} 个任务失败")
        return results
