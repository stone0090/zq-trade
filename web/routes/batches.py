"""批次管理 API"""
import uuid
import threading
from datetime import datetime
from fastapi import APIRouter, HTTPException

from web.database import get_db
from web.models import BatchCreate, BatchResponse, BatchProgress
from web.config import DB_PATH, CHARTS_DIR
from web.services.analysis import analyze_batch_sync

router = APIRouter(prefix="/api/batches", tags=["batches"])


@router.post("", response_model=BatchResponse)
def create_batch(req: BatchCreate):
    batch_id = str(uuid.uuid4())
    now = datetime.now().isoformat()
    name = req.name or f"批次 {datetime.now().strftime('%m-%d %H:%M')}"
    symbols = [s.strip() for s in req.symbols if s.strip()]

    if not symbols:
        raise HTTPException(400, "股票列表不能为空")

    with get_db() as conn:
        conn.execute(
            "INSERT INTO batches (id, name, created_at, status, total_count) VALUES (?,?,?,?,?)",
            (batch_id, name, now, 'pending', len(symbols))
        )
        for sym in symbols:
            stock_id = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO stocks (id, batch_id, symbol, end_date, created_at) VALUES (?,?,?,?,?)",
                (stock_id, batch_id, sym, req.end_date, now)
            )

        row = conn.execute("SELECT * FROM batches WHERE id=?", (batch_id,)).fetchone()

    return _row_to_batch(row)


@router.get("", response_model=list[BatchResponse])
def list_batches():
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM batches ORDER BY created_at DESC"
        ).fetchall()
    return [_row_to_batch(r) for r in rows]


@router.get("/{batch_id}", response_model=BatchResponse)
def get_batch(batch_id: str):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM batches WHERE id=?", (batch_id,)).fetchone()
    if not row:
        raise HTTPException(404, "批次不存在")
    return _row_to_batch(row)


@router.post("/{batch_id}/analyze")
def trigger_analyze(batch_id: str):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM batches WHERE id=?", (batch_id,)).fetchone()
        if not row:
            raise HTTPException(404, "批次不存在")
        if row['status'] == 'running':
            raise HTTPException(400, "分析正在进行中")

        # 重置状态
        conn.execute(
            "UPDATE batches SET status='running', completed_count=0 WHERE id=?",
            (batch_id,)
        )
        conn.execute(
            "UPDATE stocks SET status='pending', error_message=NULL, score_card_json=NULL, chart_path=NULL WHERE batch_id=?",
            (batch_id,)
        )

    # 后台线程执行分析
    t = threading.Thread(
        target=analyze_batch_sync,
        args=(batch_id, str(DB_PATH), str(CHARTS_DIR)),
        daemon=True
    )
    t.start()

    return {"message": "分析已启动"}


@router.get("/{batch_id}/progress", response_model=BatchProgress)
def get_progress(batch_id: str):
    with get_db() as conn:
        row = conn.execute(
            "SELECT status, total_count, completed_count FROM batches WHERE id=?",
            (batch_id,)
        ).fetchone()
    if not row:
        raise HTTPException(404, "批次不存在")
    return BatchProgress(
        status=row['status'],
        total_count=row['total_count'],
        completed_count=row['completed_count'],
    )


@router.delete("/{batch_id}")
def delete_batch(batch_id: str):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM batches WHERE id=?", (batch_id,)).fetchone()
        if not row:
            raise HTTPException(404, "批次不存在")
        conn.execute("DELETE FROM labels WHERE stock_id IN (SELECT id FROM stocks WHERE batch_id=?)", (batch_id,))
        conn.execute("DELETE FROM stocks WHERE batch_id=?", (batch_id,))
        conn.execute("DELETE FROM batches WHERE id=?", (batch_id,))
    return {"message": "已删除"}


def _row_to_batch(row) -> BatchResponse:
    return BatchResponse(
        id=row['id'],
        name=row['name'],
        created_at=row['created_at'],
        status=row['status'],
        total_count=row['total_count'],
        completed_count=row['completed_count'],
        labeled_count=row['labeled_count'],
    )
