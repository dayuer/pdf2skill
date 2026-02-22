"""
全局配置 — 集中管理 API 凭证、模型参数、处理阈值。

通过环境变量或 .env 文件配置，不再硬编码敏感信息。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# ── 自动加载 .env ──────────────────────────────
_env_path = Path(__file__).resolve().parent.parent / ".env"
if _env_path.exists():
    with open(_env_path, encoding="utf-8") as _f:
        for _line in _f:
            _line = _line.strip()
            if not _line or _line.startswith("#"):
                continue
            if "=" in _line:
                _k, _, _v = _line.partition("=")
                _k = _k.strip()
                _v = _v.strip().strip("\"'")
                os.environ.setdefault(_k, _v)


@dataclass
class LLMConfig:
    """大模型 API 配置"""

    base_url: str = ""
    api_key: str = ""
    model: str = "DeepSeek-R1"
    temperature: float = 0.7
    max_tokens: int = 8192
    timeout: int = 600

    def __post_init__(self) -> None:
        self.base_url = os.getenv("LLM_BASE_URL", self.base_url)
        self.api_key = os.getenv("LLM_API_KEY", self.api_key)
        self.model = os.getenv("LLM_MODEL", self.model)


@dataclass
class ChunkConfig:
    """切分配置"""

    max_chunk_chars: int = 4000
    min_chunk_chars: int = 200
    split_level: int = 2
    sliding_window_chars: int = 3000
    sliding_overlap_ratio: float = 0.2
    min_heading_count: int = 5


@dataclass
class PipelineConfig:
    """Pipeline 全局配置"""

    llm: LLMConfig = field(default_factory=LLMConfig)
    chunk: ChunkConfig = field(default_factory=ChunkConfig)
    max_concurrent_requests: int = 2
    max_retries: int = 5
    retry_base_delay: float = 3.0
    retry_max_delay: float = 120.0
    output_dir: str = "output"
    log_dir: str = "logs"
    dedup_similarity_threshold: float = 0.88


config = PipelineConfig()
