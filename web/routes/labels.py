"""标注管理 API"""
import uuid
from datetime import datetime
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import PlainTextResponse
from typing import Optional

from web.database import get_db
from web.models import LabelUpsert
from web.services.export import export_csv, sync_labels_to_csv

router = APIRouter(prefix="/api", tags=["labels"])


@router.put("/stocks/{stock_id}/label")
def upsert_label(stock_id: str, req: LabelUpsert):
    now = datetime.now().isoformat()

    with get_db() as conn:
        # 确认股票存在
        stock = conn.execute("SELECT id FROM stocks WHERE id=?", (stock_id,)).fetchone()
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

        # 更新 stocks.updated_at
        conn.execute("UPDATE stocks SET updated_at=? WHERE id=?", (now, stock_id))

    # 同步写入 labeled_cases.csv
    with get_db() as conn:
        sync_labels_to_csv(conn)

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
def export(tag_id: Optional[str] = Query(None)):
    from urllib.parse import quote
    with get_db() as conn:
        filename = "labels_all.csv"
        if tag_id:
            tag = conn.execute("SELECT name FROM tags WHERE id=?", (tag_id,)).fetchone()
            if not tag:
                raise HTTPException(404, "标签不存在")
            filename = f"labels_{tag['name']}.csv"
        csv_content = export_csv(conn, tag_id)

    # RFC 5987: 用 filename* 支持非 ASCII 文件名
    encoded = quote(filename, safe='')
    return PlainTextResponse(
        content=csv_content,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded}"}
    )
