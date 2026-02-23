"""FastAPI 依赖注入 — 公共 DI 函数"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException

from .notebook_store import FileNotebook


async def get_notebook(notebook_id: str) -> FileNotebook:
    """注入笔记本实例，自动校验存在性。"""
    nb = FileNotebook(notebook_id)
    if not nb.load_meta():
        raise HTTPException(status_code=404, detail=f"笔记本 {notebook_id} 不存在")
    return nb


# 类型别名 — 路由函数中 `nb: NotebookDep` 即可
NotebookDep = Annotated[FileNotebook, Depends(get_notebook)]
