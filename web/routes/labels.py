"""标注管理 API"""
import uuid
from datetime import datetime
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import PlainTextResponse

from web.database import get_db
from web.models import LabelUpsert
from web.services.export import export_batch_csv

router = APIRouter(prefix="/api", tags=["labels"])


@router.put("/stocks/{stock_id}/label")
def upsert_label(stock_id: str, req: LabelUpsert):
    now = datetime.now().isoformat()

    with get_db() as conn:
        # 确认股票存在
        stock = conn.execute("SELECT id, batch_id FROM stocks WHERE id=?", (stock_id,)).fetchone()
        if not stock:
            raise HTTPException(404, "股票不存在")

        # upsert 标注
        existing = conn.execute("SELECT id FROM labels WHERE stock_id=?", (stock_id,)).fetchone()

        if existing:
            conn.execute("""
                UPDATE labels SET
                    dl_grade=?, dl_note=?,
                    pt_grade=?, pt_note=?,
                    lk_grade=?, lk_note=?,
                    sf_grade=?, sf_note=?,
                    ty_grade=?, ty_note=?,
                    dn_grade=?, dn_note=?,
                    verdict=?, reason=?,
                    updated_at=?
                WHERE stock_id=?
            """, (
                req.dl_grade, req.dl_note,
                req.pt_grade, req.pt_note,
                req.lk_grade, req.lk_note,
                req.sf_grade, req.sf_note,
                req.ty_grade, req.ty_note,
                req.dn_grade, req.dn_note,
                req.verdict, req.reason,
                now, stock_id,
            ))
        else:
            label_id = str(uuid.uuid4())
            conn.execute("""
                INSERT INTO labels (
                    id, stock_id,
                    dl_grade, dl_note,
                    pt_grade, pt_note,
                    lk_grade, lk_note,
                    sf_grade, sf_note,
                    ty_grade, ty_note,
                    dn_grade, dn_note,
                    verdict, reason,
                    created_at, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                label_id, stock_id,
                req.dl_grade, req.dl_note,
                req.pt_grade, req.pt_note,
                req.lk_grade, req.lk_note,
                req.sf_grade, req.sf_note,
                req.ty_grade, req.ty_note,
                req.dn_grade, req.dn_note,
                req.verdict, req.reason,
                now, now,
            ))

        # 更新批次的 labeled_count
        batch_id = stock['batch_id']
        count = conn.execute(
            "SELECT COUNT(*) as c FROM labels WHERE stock_id IN (SELECT id FROM stocks WHERE batch_id=?)",
            (batch_id,)
        ).fetchone()['c']
        conn.execute(
            "UPDATE batches SET labeled_count=? WHERE id=?",
            (count, batch_id)
        )

    return {"message": "标注已保存"}


@router.get("/stocks/{stock_id}/label")
def get_label(stock_id: str):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM labels WHERE stock_id=?", (stock_id,)).fetchone()
    if not row:
        return None
    return {
        'dl_grade': row['dl_grade'], 'dl_note': row['dl_note'],
        'pt_grade': row['pt_grade'], 'pt_note': row['pt_note'],
        'lk_grade': row['lk_grade'], 'lk_note': row['lk_note'],
        'sf_grade': row['sf_grade'], 'sf_note': row['sf_note'],
        'ty_grade': row['ty_grade'], 'ty_note': row['ty_note'],
        'dn_grade': row['dn_grade'], 'dn_note': row['dn_note'],
        'verdict': row['verdict'], 'reason': row['reason'],
    }


@router.get("/export")
def export_csv(batch_id: str = Query(...)):
    with get_db() as conn:
        batch = conn.execute("SELECT * FROM batches WHERE id=?", (batch_id,)).fetchone()
        if not batch:
            raise HTTPException(404, "批次不存在")
        csv_content = export_batch_csv(conn, batch_id)

    return PlainTextResponse(
        content=csv_content,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=labels_{batch['name']}.csv"}
    )
