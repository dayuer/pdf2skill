"""
全局配置 — 集中管理 API 凭证、模型参数、处理阈值。

基于 pydantic-settings 自动解析 .env + 环境变量。
新增 config_hash 配置指纹，用于缓存失效判断。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings


# ── 项目根目录 ──────────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ENV_PATH = _PROJECT_ROOT / ".env"


class LLMConfig(BaseSettings):
    """大模型 API 配置"""

    base_url: str = ""
    api_key: str = ""
    model: str = "DeepSeek-R1"
    temperature: float = 0.7
    max_tokens: int = 4096
    timeout: int = 600

    model_config = {
        "env_prefix": "LLM_",
        "env_file": str(_ENV_PATH),
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


class EmbeddingConfig(BaseSettings):
    """Embedding 模型配置（向量检索用，可选）"""

    base_url: str = ""
    api_key: str = ""
    model: str = ""
    dim: int = 1024

    model_config = {
        "env_prefix": "EMBEDDING_",
        "env_file": str(_ENV_PATH),
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }

    @property
    def is_configured(self) -> bool:
        """Embedding 是否已配置（有 API Key 和 Model 即视为可用）"""
        return bool(self.api_key and self.model)


class ChunkConfig(BaseSettings):
    """切分配置"""

    max_chunk_chars: int = 2000
    min_chunk_chars: int = 200
    split_level: int = 2
    sliding_window_chars: int = 3000
    sliding_overlap_ratio: float = 0.2
    min_heading_count: int = 5

    model_config = {
        "env_prefix": "CHUNK_",
        "env_file": str(_ENV_PATH),
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


class PipelineConfig(BaseSettings):
    """Pipeline 全局配置"""

    llm: LLMConfig = Field(default_factory=LLMConfig)
    chunk: ChunkConfig = Field(default_factory=ChunkConfig)
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)

    max_concurrent_requests: int = 2
    max_retries: int = 5
    retry_base_delay: float = 3.0
    retry_max_delay: float = 120.0
    output_dir: str = "output"
    log_dir: str = "logs"
    dedup_similarity_threshold: float = 0.88

    # 向量存储开关（有 Embedding 配置时自动启用）
    use_milvus: bool = False
    milvus_db_path: str = "data/milvus.db"

    model_config = {
        "env_file": str(_ENV_PATH),
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }

    @property
    def config_hash(self) -> str:
        """
        关键配置指纹 — MD5 哈希。

        配置变更时哈希值不同，可用于缓存失效判断。
        """
        key_content = (
            f"{self.llm.base_url}|"
            f"{self.llm.api_key}|"
            f"{self.llm.model}|"
            f"{self.embedding.api_key}|"
            f"{self.embedding.model}"
        )
        return hashlib.md5(key_content.encode("utf-8")).hexdigest()

    def ensure_filesystem(self) -> None:
        """启动时自动创建所有必要的目录"""
        dirs = [
            _PROJECT_ROOT / "notebooks",
            _PROJECT_ROOT / self.output_dir,
            _PROJECT_ROOT / self.log_dir,
            _PROJECT_ROOT / "data",
        ]
        for d in dirs:
            d.mkdir(parents=True, exist_ok=True)


config = PipelineConfig()
config.ensure_filesystem()
