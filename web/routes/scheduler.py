"""定时任务管理 API"""
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional

from scheduler.engine import (
    get_all_jobs_status, pause_job, resume_job, run_job_now,
    update_job_trigger, update_job_config, update_jobs_order,
)
from web.database import get_db

router = APIRouter(prefix="/api/scheduler", tags=["scheduler"])


class UpdateJobTriggerReq(BaseModel):
    trigger: str  # 'cron' or 'interval'
    trigger_args: dict  # e.g. {"minutes": 10} or {"hour": 8, "minute": 30}


class UpdateJobConfigReq(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None


class ReorderJobsReq(BaseModel):
    job_ids: list


@router.get("/jobs")
def list_jobs():
    """获取所有任务状态"""
    return get_all_jobs_status()


@router.post("/jobs/{job_id}/pause")
def api_pause_job(job_id: str):
    ok = pause_job(job_id)
    return {"ok": ok}


@router.post("/jobs/{job_id}/resume")
def api_resume_job(job_id: str):
    ok = resume_job(job_id)
    return {"ok": ok}


@router.post("/jobs/{job_id}/run")
def api_run_job(job_id: str):
    ok = run_job_now(job_id)
    return {"ok": ok}


@router.put("/jobs/{job_id}/trigger")
def api_update_trigger(job_id: str, req: UpdateJobTriggerReq):
    """更新任务调度周期"""
    if req.trigger not in ('cron', 'interval'):
        raise HTTPException(400, "trigger 必须是 cron 或 interval")
    ok = update_job_trigger(job_id, req.trigger, req.trigger_args)
    if not ok:
        raise HTTPException(400, f"任务 {job_id} 不存在或更新失败")
    return {"ok": True, "message": f"已更新 {job_id} 的调度周期"}


@router.put("/jobs/{job_id}/config")
def api_update_config(job_id: str, req: UpdateJobConfigReq):
    """更新任务名称和描述"""
    ok = update_job_config(job_id, req.name, req.description)
    if not ok:
        raise HTTPException(400, f"任务 {job_id} 不存在或更新失败")
    return {"ok": True}


@router.put("/jobs/reorder")
def api_reorder_jobs(req: ReorderJobsReq):
    """调整任务显示顺序"""
    ok = update_jobs_order(req.job_ids)
    if not ok:
        raise HTTPException(400, "更新排序失败")
    return {"ok": True}


@router.get("/logs")
def list_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    job_name: Optional[str] = Query(None),
):
    """获取任务执行日志（分页）"""
    with get_db() as conn:
        conditions = []
        params = []

        if job_name:
            conditions.append("job_name = ?")
            params.append(job_name)

        where = "WHERE " + " AND ".join(conditions) if conditions else ""

        # 总数
        total = conn.execute(
            f"SELECT COUNT(*) as cnt FROM job_logs {where}", params
        ).fetchone()['cnt']

        # 分页查询
        offset = (page - 1) * page_size
        rows = conn.execute(
            f"SELECT * FROM job_logs {where} ORDER BY started_at DESC LIMIT ? OFFSET ?",
            params + [page_size, offset]
        ).fetchall()

    return {
        "items": [dict(r) for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size if page_size > 0 else 0,
    }
