"""工作流引擎 API：execute / save / load"""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException

from .notebook_store import FileNotebook
from .workflow_engine import WorkflowEngine

router = APIRouter(prefix="/api/workflow", tags=["workflow"])

_workflow_engine = WorkflowEngine()


@router.post("/execute")
async def api_workflow_execute(payload: dict):
    """执行 JSON 工作流定义。"""
    notebook_id = payload.get("session_id") or payload.get("notebook_id")
    workflow_def = payload.get("workflow")
    if not notebook_id or not workflow_def:
        raise HTTPException(400, "需要 notebook_id 和 workflow")

    nb = FileNotebook(notebook_id)
    run = _workflow_engine.parse(workflow_def)

    results: list[dict] = []

    def on_status(node_id, status, data):
        results.append({"node_id": node_id, "status": status.value, "data": data})

    await _workflow_engine.execute(
        run,
        context={"notebook": nb, "notebook_id": notebook_id},
        on_status=on_status,
    )

    return {
        "run_id": run.run_id, "status": run.status,
        "elapsed_s": run.elapsed_s, "results": results,
        "summary": _workflow_engine.to_json(run),
    }


@router.post("/save")
async def api_workflow_save(payload: dict):
    """保存工作流定义。"""
    notebook_id = payload.get("session_id") or payload.get("notebook_id")
    workflow = payload.get("workflow")
    if not notebook_id or not workflow:
        raise HTTPException(400, "需要 notebook_id 和 workflow")

    nb = FileNotebook(notebook_id)
    wf_path = nb.root / "workflow.json"
    wf_path.write_text(json.dumps(workflow, ensure_ascii=False, indent=2))
    return {"saved": True, "path": str(wf_path)}


@router.get("/load/{notebook_id}")
async def api_workflow_load(notebook_id: str):
    """加载已保存的工作流定义。"""
    nb = FileNotebook(notebook_id)
    wf_path = nb.root / "workflow.json"
    if not wf_path.exists():
        return {"workflow": None}
    return {"workflow": json.loads(wf_path.read_text())}
