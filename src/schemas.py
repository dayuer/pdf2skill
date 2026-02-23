"""Pydantic V2 请求/响应模型"""

from __future__ import annotations

from pydantic import BaseModel, Field


# ── 请求模型 ──

class SettingsUpdate(BaseModel):
    """调整提取设置"""
    book_type: str | None = None
    prompt_type: str | None = None
    system_prompt: str | None = None


class RechunkRequest(BaseModel):
    """重新切片参数"""
    max_chars: int = Field(default=2000, ge=200, le=10000)
    min_chars: int = Field(default=200, ge=50, le=2000)


class TuneRequest(BaseModel):
    """单 chunk 调优"""
    chunk_index: int = 0
    prompt_hint: str = ""
    system_prompt: str = ""


class SampleRequest(BaseModel):
    """抽样验证"""
    sample_size: int = Field(default=5, ge=1, le=50)


class WorkflowExecuteRequest(BaseModel):
    """工作流执行 — 支持 n8n 式 connections 和旧版 edges 两种格式"""
    workflow_id: str | None = Field(default=None, alias="session_id")
    workflow: dict

    class Config:
        populate_by_name = True


class WorkflowSaveRequest(BaseModel):
    """工作流保存"""
    workflow_id: str | None = Field(default=None, alias="session_id")
    workflow: dict

    class Config:
        populate_by_name = True


class PinDataRequest(BaseModel):
    """固定节点数据（用于调试）"""
    node_id: str
    data: list[dict] = Field(default_factory=list)
