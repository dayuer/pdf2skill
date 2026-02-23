"""
pdf2skill API — FastAPI 入口

纯 API 服务，前端由 Vite dev server 或 Nginx 独立提供。
路由按职责拆分为 5 个模块：
  - api_analyze  : 上传/分析/设置/重切/prompt-preview
  - api_tune     : chunk 列表/调优/历史/抽验/预览
  - api_execute  : SSE 全量执行/笔记本列表/状态
  - api_skills   : Skills CRUD/注册表/图谱/向量检索
  - api_workflow : 工作流 execute/save/load
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api_analyze import router as analyze_router
from .api_tune import router as tune_router
from .api_execute import router as execute_router
from .api_skills import router as skills_router
from .api_workflow import router as workflow_router

app = FastAPI(title="pdf2skill", version="0.4")

# CORS — 开发模式允许 Vite dev server
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(analyze_router)
app.include_router(tune_router)
app.include_router(execute_router)
app.include_router(skills_router)
app.include_router(workflow_router)
