"""FastAPI 依赖注入 — 公共 DI 函数"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException

from .workflow_store import FileWorkflow


async def get_workflow(workflow_id: str) -> FileWorkflow:
    """注入工作流实例，自动校验存在性。"""
    nb = FileWorkflow(workflow_id)
    if not nb.load_meta():
        raise HTTPException(status_code=404, detail=f"工作流 {workflow_id} 不存在")
    return nb


# 类型别名 — 路由函数中 `nb: WorkflowDep` 即可
WorkflowDep = Annotated[FileWorkflow, Depends(get_workflow)]
