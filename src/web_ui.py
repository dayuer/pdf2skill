"""
pdf2skill API — FastAPI 入口

纯 API 服务，前端由 Vite dev server 或 Nginx 独立提供。
路由按职责拆分为 5 个模块：
  - api_analyze  : 上传/分析/设置/重切/prompt-preview
  - api_tune     : chunk 列表/调优/历史/抽验/预览
  - api_execute  : SSE 全量执行/工作流列表/状态
  - api_skills   : Skills CRUD/注册表/图谱/向量检索
  - api_workflow : 工作流 execute/save/load
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

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

# 注册 API 路由
app.include_router(analyze_router)
app.include_router(tune_router)
app.include_router(execute_router)
app.include_router(skills_router)
app.include_router(workflow_router)

# 前端静态文件（生产环境 vite build 产物）
_DIST_DIR = Path(__file__).parent.parent / "static" / "dist"
if _DIST_DIR.exists():
    app.mount("/assets", StaticFiles(directory=_DIST_DIR / "assets"), name="assets")

    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str):
        """SPA fallback — 非 /api 路径都返回 index.html"""
        file = _DIST_DIR / full_path
        if file.is_file():
            return FileResponse(file)
        return FileResponse(_DIST_DIR / "index.html")
