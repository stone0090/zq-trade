"""监控列表 API"""
from fastapi import APIRouter, HTTPException, Query
from typing import Optional

from web.database import get_db
from web.services.state_machine import transition_stock, get_stocks_by_watch_status
from web.services.monitor import refresh_latest_prices

router = APIRouter(prefix="/api/monitor", tags=["monitor"])


@router.get("/stocks")
def list_monitor_stocks(
    watch_status: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    market: Optional[str] = Query(None),
    dl: Optional[str] = Query(None),
    pt: Optional[str] = Query(None),
    lk: Optional[str] = Query(None),
    sf: Optional[str] = Query(None),
    ty: Optional[str] = Query(None),
    dn: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=10000),
):
    """获取监控列表品种"""
    with get_db() as conn:
        conditions = []
        params = []

        if watch_status:
            statuses = watch_status.split(",")
            placeholders = ",".join("?" * len(statuses))
            conditions.append(f"s.watch_status IN ({placeholders})")
            params.extend(statuses)
        else:
            conditions.append("s.watch_status IN ('watching','focused','holding')")

        if search:
            conditions.append("(s.symbol LIKE ? OR s.symbol_name LIKE ?)")
            params.extend([f"%{search}%", f"%{search}%"])

        if market:
            conditions.append("s.market = ?")
            params.append(market)

        # 各维度评级筛选
        _grade_map = {'S': ('S',), 'A': ('S', 'A'), 'B': ('S', 'A', 'B')}
        _sf_map = {'1st': ('1st',), '2nd': ('1st', '2nd')}
        for col, val, mapping in [
            ('dl_grade', dl, _grade_map),
            ('pt_grade', pt, _grade_map),
            ('lk_grade', lk, _grade_map),
            ('ty_grade', ty, _grade_map),
            ('dn_grade', dn, _grade_map),
            ('sf_grade', sf, _sf_map),
        ]:
            if val and val in mapping:
                allowed = mapping[val]
                ph = ','.join('?' * len(allowed))
                conditions.append(f"s.{col} IN ({ph})")
                params.extend(allowed)

        where = "WHERE " + " AND ".join(conditions) if conditions else ""

        rows = conn.execute(f"""
            SELECT s.id, s.symbol, s.symbol_name, s.market, s.watch_status,
                   s.last_price, s.last_price_time, s.news_alert,
                   s.dl_grade, s.pt_grade, s.lk_grade,
                   s.sf_grade, s.ty_grade, s.dn_grade,
                   s.analyzed_at, s.updated_at, s.kline_end_time
            FROM stocks s
            {where}
            ORDER BY
                CASE s.watch_status
                    WHEN 'focused' THEN 1
                    WHEN 'holding' THEN 2
                    WHEN 'watching' THEN 3
                END,
                s.updated_at DESC
        """, params).fetchall()

    total = len(rows)
    total_pages = max(1, (total + page_size - 1) // page_size)
    offset = (page - 1) * page_size
    paged = rows[offset:offset + page_size]

    return {
        "items": [dict(r) for r in paged],
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
    }


@router.get("/stats")
def monitor_stats():
    """监控列表统计"""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT watch_status, COUNT(*) as cnt
            FROM stocks
            WHERE watch_status IN ('watching', 'focused', 'holding')
            GROUP BY watch_status
        """).fetchall()
    stats = {r['watch_status']: r['cnt'] for r in rows}
    return {
        "focused": stats.get("focused", 0),
        "watching": stats.get("watching", 0),
        "holding": stats.get("holding", 0),
    }


@router.post("/{stock_id}/upgrade")
def upgrade_stock(stock_id: str, target: str = Query(...)):
    """手动升级品种状态"""
    r = transition_stock(stock_id, target, "手动升级")
    if not r["ok"]:
        raise HTTPException(400, r["message"])
    return r


@router.post("/{stock_id}/downgrade")
def downgrade_stock(stock_id: str, target: str = Query(...)):
    """手动降级品种状态"""
    r = transition_stock(stock_id, target, "手动降级")
    if not r["ok"]:
        raise HTTPException(400, r["message"])
    return r


@router.post("/{stock_id}/remove")
def remove_from_monitor(stock_id: str):
    """从监控列表移除"""
    r = transition_stock(stock_id, "removed", "手动移除")
    if not r["ok"]:
        raise HTTPException(400, r["message"])
    return r


@router.post("/refresh-prices")
def api_refresh_prices():
    """手动刷新监控品种价格"""
    summary = refresh_latest_prices()
    return {"ok": True, "message": summary}


@router.post("/{stock_id}/buy")
def buy_stock(stock_id: str):
    """买入品种：focused -> holding"""
    r = transition_stock(stock_id, "holding", "买入")
    if not r["ok"]:
        raise HTTPException(400, r["message"])
    return r


from pydantic import BaseModel

class BatchActionReq(BaseModel):
    stock_ids: list
    action: str

@router.post("/batch-action")
def batch_action(req: BatchActionReq):
    """批量操作：升级/降级/移除/买入"""
    from web.services.state_machine import batch_transition
    
    action_map = {
        "upgrade_watching": "watching",
        "upgrade_focused": "focused",
        "downgrade_idle": "idle",
        "downgrade_watching": "watching",
        "remove": "removed",
        "buy": "holding",
    }
    target = action_map.get(req.action)
    if not target:
        raise HTTPException(400, f"未知操作: {req.action}")
    
    result = batch_transition(req.stock_ids, target, f"批量操作: {req.action}")
    return result
