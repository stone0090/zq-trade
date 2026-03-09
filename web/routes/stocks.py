"""股票管理 API"""
import json
from pathlib import Path
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from web.database import get_db
from web.models import StockListItem, StockDetail

router = APIRouter(prefix="/api/stocks", tags=["stocks"])


@router.get("", response_model=list[StockListItem])
def list_stocks(batch_id: str = Query(...)):
    with get_db() as conn:
        rows = conn.execute("""
            SELECT s.id, s.symbol, s.symbol_name, s.market, s.end_date, s.status,
                   COALESCE(s.dl_grade, l.dl_grade) as dl_grade,
                   COALESCE(s.pt_grade, l.pt_grade) as pt_grade,
                   COALESCE(s.lk_grade, l.lk_grade) as lk_grade,
                   COALESCE(s.sf_grade, l.sf_grade) as sf_grade,
                   COALESCE(s.ty_grade, l.ty_grade) as ty_grade,
                   COALESCE(s.dn_grade, l.dn_grade) as dn_grade,
                   COALESCE(s.conclusion, l.reason) as conclusion,
                   COALESCE(s.position_size, l.verdict) as position_size,
                   s.analyzed_at,
                   CASE WHEN l.id IS NOT NULL THEN 'labeled' ELSE 'unlabeled' END as label_status
            FROM stocks s
            LEFT JOIN labels l ON l.stock_id = s.id
            WHERE s.batch_id = ?
            ORDER BY s.created_at
        """, (batch_id,)).fetchall()

    return [_row_to_list_item(r) for r in rows]


@router.get("/{stock_id}", response_model=StockDetail)
def get_stock(stock_id: str):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM stocks WHERE id=?", (stock_id,)).fetchone()
        if not row:
            raise HTTPException(404, "股票不存在")

        label_row = conn.execute(
            "SELECT * FROM labels WHERE stock_id=?", (stock_id,)
        ).fetchone()

    score_card = None
    if row['score_card_json']:
        try:
            score_card = json.loads(row['score_card_json'])
        except (json.JSONDecodeError, TypeError):
            pass

    label = None
    if label_row:
        label = {
            'dl_grade': label_row['dl_grade'],
            'dl_note': label_row['dl_note'],
            'pt_grade': label_row['pt_grade'],
            'pt_note': label_row['pt_note'],
            'lk_grade': label_row['lk_grade'],
            'lk_note': label_row['lk_note'],
            'sf_grade': label_row['sf_grade'],
            'sf_note': label_row['sf_note'],
            'ty_grade': label_row['ty_grade'],
            'ty_note': label_row['ty_note'],
            'dn_grade': label_row['dn_grade'],
            'dn_note': label_row['dn_note'],
            'verdict': label_row['verdict'],
            'reason': label_row['reason'],
        }

    return StockDetail(
        id=row['id'],
        symbol=row['symbol'],
        symbol_name=row['symbol_name'],
        market=row['market'],
        end_date=row['end_date'],
        status=row['status'],
        error_message=row['error_message'],
        score_card=score_card,
        dl_grade=row['dl_grade'],
        pt_grade=row['pt_grade'],
        lk_grade=row['lk_grade'],
        sf_grade=row['sf_grade'],
        ty_grade=row['ty_grade'],
        dn_grade=row['dn_grade'],
        conclusion=row['conclusion'],
        position_size=row['position_size'],
        label=label,
        analyzed_at=row['analyzed_at'],
    )


@router.get("/{stock_id}/chart")
def get_chart(stock_id: str):
    with get_db() as conn:
        row = conn.execute(
            "SELECT chart_path FROM stocks WHERE id=?", (stock_id,)
        ).fetchone()
    if not row or not row['chart_path']:
        raise HTTPException(404, "图表不存在")

    chart_path = Path(row['chart_path'])
    if not chart_path.exists():
        raise HTTPException(404, "图表文件不存在")

    return FileResponse(str(chart_path), media_type="image/png")


def _row_to_list_item(row) -> StockListItem:
    return StockListItem(
        id=row['id'],
        symbol=row['symbol'],
        symbol_name=row['symbol_name'],
        market=row['market'],
        end_date=row['end_date'],
        status=row['status'],
        dl_grade=row['dl_grade'],
        pt_grade=row['pt_grade'],
        lk_grade=row['lk_grade'],
        sf_grade=row['sf_grade'],
        ty_grade=row['ty_grade'],
        dn_grade=row['dn_grade'],
        conclusion=row['conclusion'],
        position_size=row['position_size'],
        label_status=row['label_status'],
        analyzed_at=row['analyzed_at'],
    )
