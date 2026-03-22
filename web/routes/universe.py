"""品种库 API"""
import uuid
import logging
import threading
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from web.database import get_db
from web.services.state_machine import transition_stock, batch_transition

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/universe", tags=["universe"])


class AddStockReq(BaseModel):
    symbol: str
    source_type: str = "manual"
    watch_status: str = "pending"


class BatchActionReq(BaseModel):
    stock_ids: list[str]
    action: str  # confirm, remove, restore, upgrade_watching, delete


def _detect_market(symbol: str) -> str:
    """根据代码自动判断市场: 纯数字6位=A股, 纯数字4-5位=港股, 其余=美股"""
    if symbol.isdigit():
        return 'cn' if len(symbol) >= 6 else 'hk'
    return 'us'


def _trigger_background_analysis(stock_id: str, symbol: str):
    """在后台线程中对单只股票执行六维分析 + 基本面分析"""
    import json
    from web.services.analysis import analyze_stock
    from web.services.fundamentals import refresh_fundamentals
    from web import config

    def _run():
        # 1. 基本面分析（先执行，即使六维分析失败也能保留基本面数据）
        try:
            refresh_fundamentals(stock_id)
        except Exception as e:
            logger.warning(f"基本面分析 {symbol} 失败: {e}")

        # 2. 六维分析
        try:
            result = analyze_stock(symbol, chart_dir=str(config.CHARTS_DIR))
            with get_db() as conn:
                conn.execute("""
                    UPDATE stocks SET
                        status='completed',
                        symbol_name=?, market=?,
                        score_card_json=?, chart_path=?,
                        dl_grade=?, pt_grade=?, lk_grade=?,
                        sf_grade=?, ty_grade=?, dn_grade=?,
                        conclusion=?, position_size=?,
                        analyzed_at=?, updated_at=?
                    WHERE id=?
                """, (
                    result['symbol_name'], result['market'],
                    json.dumps(result['score_card'], ensure_ascii=False),
                    result['chart_path'],
                    result['grades']['dl_grade'],
                    result['grades']['pt_grade'],
                    result['grades']['lk_grade'],
                    result['grades']['sf_grade'],
                    result['grades']['ty_grade'],
                    result['grades']['dn_grade'],
                    result['conclusion'],
                    result['position_size'],
                    datetime.now().isoformat(),
                    datetime.now().isoformat(),
                    stock_id,
                ))
        except Exception as e:
            logger.warning(f"自动分析 {symbol} 失败: {e}")
            try:
                with get_db() as conn:
                    conn.execute(
                        "UPDATE stocks SET status='error', error_message=? WHERE id=?",
                        (str(e)[:500], stock_id)
                    )
            except Exception:
                pass

    t = threading.Thread(target=_run, daemon=True)
    t.start()


@router.get("/stocks")
def list_universe_stocks(
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
    """获取品种库股票列表"""
    with get_db() as conn:
        conditions = []
        params = []

        if watch_status:
            statuses = watch_status.split(",")
            placeholders = ",".join("?" * len(statuses))
            conditions.append(f"s.watch_status IN ({placeholders})")
            params.extend(statuses)
        else:
            conditions.append("s.watch_status IN ('pending','idle','removed')")

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
                placeholders = ','.join('?' * len(allowed))
                conditions.append(f"s.{col} IN ({placeholders})")
                params.extend(allowed)

        where = "WHERE " + " AND ".join(conditions) if conditions else ""

        rows = conn.execute(f"""
            SELECT s.id, s.symbol, s.symbol_name, s.market, s.watch_status,
                   s.source_type, s.last_price, s.last_price_time,
                   s.fundamental_json, s.news_alert, s.status,
                   s.dl_grade, s.pt_grade, s.lk_grade,
                   s.sf_grade, s.ty_grade, s.dn_grade,
                   s.analyzed_at, s.created_at, s.updated_at
            FROM stocks s
            {where}
            ORDER BY s.updated_at DESC, s.created_at DESC
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


@router.post("/add")
def add_to_universe(req: AddStockReq):
    """手动添加品种到品种库，自动检测市场并触发六维分析"""
    symbol = req.symbol.strip().upper()
    if not symbol:
        raise HTTPException(400, "股票代码不能为空")

    now = datetime.now().isoformat()
    stock_id = str(uuid.uuid4())
    market = _detect_market(symbol)

    with get_db() as conn:
        # 检查是否已存在
        existing = conn.execute(
            "SELECT id, watch_status FROM stocks WHERE symbol=? AND COALESCE(end_date,'')=''",
            (symbol,)
        ).fetchone()

        if existing:
            ws = existing['watch_status'] or 'none'
            if ws == 'removed':
                transition_stock(existing['id'], 'idle', '重新添加')
                _trigger_background_analysis(existing['id'], symbol)
                return {"ok": True, "stock_id": existing['id'], "message": "已从移除状态恢复，正在自动分析..."}
            if ws != 'none':
                return {"ok": False, "message": f"该品种已在品种库中 (状态: {ws})"}
            conn.execute(
                "UPDATE stocks SET watch_status=?, source_type=?, market=?, updated_at=? WHERE id=?",
                (req.watch_status, req.source_type, market, now, existing['id'])
            )
            _trigger_background_analysis(existing['id'], symbol)
            return {"ok": True, "stock_id": existing['id'], "message": "已加入品种库，正在自动分析..."}

        conn.execute(
            """INSERT INTO stocks (id, symbol, market, watch_status, source_type, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (stock_id, symbol, market, req.watch_status, req.source_type, now, now)
        )

    _trigger_background_analysis(stock_id, symbol)
    return {"ok": True, "stock_id": stock_id, "message": "已添加到品种库，正在自动分析..."}


@router.post("/batch-action")
def batch_action(req: BatchActionReq):
    """批量操作：确认/移除/恢复/删除/升级"""
    if req.action == "delete":
        return _batch_delete(req.stock_ids)

    action_map = {
        "confirm": "idle",
        "remove": "removed",
        "restore": "pending",
        "upgrade_watching": "watching",
        "upgrade_focused": "focused",
    }
    target = action_map.get(req.action)
    if not target:
        raise HTTPException(400, f"未知操作: {req.action}")

    result = batch_transition(req.stock_ids, target, f"批量操作: {req.action}")
    return result


def _batch_delete(stock_ids: list) -> dict:
    """彻底删除品种（仅允许删除 removed 状态的）"""
    deleted = 0
    errors = []
    for sid in stock_ids:
        with get_db() as conn:
            row = conn.execute(
                "SELECT id, symbol, watch_status FROM stocks WHERE id=?", (sid,)
            ).fetchone()
            if not row:
                errors.append(f"{sid}: 不存在")
                continue
            if row['watch_status'] != 'removed':
                errors.append(f"{row['symbol']}: 仅可删除已移除的品种")
                continue
            conn.execute("DELETE FROM labels WHERE stock_id=?", (sid,))
            conn.execute("DELETE FROM stock_tags WHERE stock_id=?", (sid,))
            conn.execute("DELETE FROM stock_news WHERE stock_id=?", (sid,))
            conn.execute("DELETE FROM paper_orders WHERE stock_id=?", (sid,))
            conn.execute("DELETE FROM stock_sources WHERE stock_id=?", (sid,))
            conn.execute("DELETE FROM stocks WHERE id=?", (sid,))
            deleted += 1
    return {"success": deleted, "failed": len(errors), "errors": errors}


@router.post("/{stock_id}/confirm")
def confirm_stock(stock_id: str):
    """确认入库：pending -> idle"""
    r = transition_stock(stock_id, "idle", "人工确认入库")
    if not r["ok"]:
        raise HTTPException(400, r["message"])
    return r


@router.post("/{stock_id}/remove")
def remove_stock(stock_id: str):
    """移除品种"""
    r = transition_stock(stock_id, "removed", "人工移除")
    if not r["ok"]:
        raise HTTPException(400, r["message"])
    return r


@router.post("/{stock_id}/restore")
def restore_stock(stock_id: str):
    """恢复已移除品种：removed -> pending"""
    r = transition_stock(stock_id, "pending", "人工恢复")
    if not r["ok"]:
        raise HTTPException(400, r["message"])
    return r


@router.delete("/{stock_id}")
def delete_stock(stock_id: str):
    """彻底删除品种（仅允许 removed 状态）"""
    with get_db() as conn:
        row = conn.execute(
            "SELECT id, symbol, watch_status FROM stocks WHERE id=?", (stock_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "品种不存在")
        if row['watch_status'] != 'removed':
            raise HTTPException(400, "仅可删除已移除的品种")

        conn.execute("DELETE FROM labels WHERE stock_id=?", (stock_id,))
        conn.execute("DELETE FROM stock_tags WHERE stock_id=?", (stock_id,))
        conn.execute("DELETE FROM stock_news WHERE stock_id=?", (stock_id,))
        conn.execute("DELETE FROM paper_orders WHERE stock_id=?", (stock_id,))
        conn.execute("DELETE FROM stock_sources WHERE stock_id=?", (stock_id,))
        conn.execute("DELETE FROM stocks WHERE id=?", (stock_id,))

    return {"ok": True, "message": f"已彻底删除 {row['symbol']}"}


@router.post("/{stock_id}/upgrade")
def upgrade_stock(stock_id: str, target: str = Query("watching")):
    """手动升级状态"""
    r = transition_stock(stock_id, target, "人工升级")
    if not r["ok"]:
        raise HTTPException(400, r["message"])
    return r


@router.get("/stats")
def universe_stats():
    """获取品种库统计（包含所有状态）"""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT watch_status, COUNT(*) as cnt
            FROM stocks
            WHERE watch_status != 'none'
            GROUP BY watch_status
        """).fetchall()
    stats = {r['watch_status']: r['cnt'] for r in rows}
    return stats


@router.post("/mock-data")
def create_mock_data():
    """生成模拟交易数据（持仓 + 已平仓）"""
    now = datetime.now().isoformat()
    mock_stocks = [
        {"symbol": "AAPL", "name": "Apple Inc.", "market": "us", "price": 178.50, "ws": "holding",
         "dl": "S", "pt": "A", "lk": "A", "sf": "1st", "ty": "B", "dn": "A"},
        {"symbol": "MSFT", "name": "Microsoft Corp.", "market": "us", "price": 415.20, "ws": "holding",
         "dl": "S", "pt": "A", "lk": "A", "sf": "1st", "ty": "A", "dn": "B"},
        {"symbol": "NVDA", "name": "NVIDIA Corp.", "market": "us", "price": 875.30, "ws": "focused",
         "dl": "S", "pt": "A", "lk": "B", "sf": "1st", "ty": "B", "dn": "A"},
        {"symbol": "TSLA", "name": "Tesla Inc.", "market": "us", "price": 245.60, "ws": "watching",
         "dl": "S", "pt": "B", "lk": "B", "sf": "2nd", "ty": "C", "dn": "B"},
        {"symbol": "AMZN", "name": "Amazon.com Inc.", "market": "us", "price": 186.40, "ws": "idle",
         "dl": "S", "pt": "B", "lk": "A", "sf": "1st", "ty": "B", "dn": "C"},
        {"symbol": "META", "name": "Meta Platforms", "market": "us", "price": 505.75, "ws": "focused",
         "dl": "S", "pt": "A", "lk": "A", "sf": "1st", "ty": "A", "dn": "B"},
        {"symbol": "GOOG", "name": "Alphabet Inc.", "market": "us", "price": 155.80, "ws": "watching",
         "dl": "S", "pt": "B", "lk": "B", "sf": "1st", "ty": "B", "dn": "C"},
    ]

    stock_ids = {}
    with get_db() as conn:
        for ms in mock_stocks:
            existing = conn.execute(
                "SELECT id FROM stocks WHERE symbol=? AND COALESCE(end_date,'')=''",
                (ms["symbol"],)
            ).fetchone()
            if existing:
                sid = existing['id']
                # 只更新名称/市场/价格/状态，不覆盖已有的分析评级
                has_analysis = conn.execute(
                    "SELECT score_card_json FROM stocks WHERE id=? AND score_card_json IS NOT NULL AND score_card_json != ''",
                    (sid,)
                ).fetchone()
                if has_analysis:
                    conn.execute("""
                        UPDATE stocks SET symbol_name=?, market=?, watch_status=?,
                            last_price=?, updated_at=?, status='completed', analyzed_at=?
                        WHERE id=?
                    """, (ms["name"], ms["market"], ms["ws"], ms["price"], now, now, sid))
                else:
                    conn.execute("""
                        UPDATE stocks SET symbol_name=?, market=?, watch_status=?,
                            last_price=?, dl_grade=?, pt_grade=?, lk_grade=?,
                            sf_grade=?, ty_grade=?, dn_grade=?, updated_at=?,
                            status='completed', analyzed_at=?
                        WHERE id=?
                    """, (ms["name"], ms["market"], ms["ws"], ms["price"],
                          ms["dl"], ms["pt"], ms["lk"], ms["sf"], ms["ty"], ms["dn"],
                          now, now, sid))
            else:
                sid = str(uuid.uuid4())
                conn.execute("""
                    INSERT INTO stocks (id, symbol, symbol_name, market, watch_status,
                        last_price, dl_grade, pt_grade, lk_grade, sf_grade, ty_grade, dn_grade,
                        source_type, status, created_at, updated_at, analyzed_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'manual', 'completed', ?, ?, ?)
                """, (sid, ms["symbol"], ms["name"], ms["market"], ms["ws"], ms["price"],
                      ms["dl"], ms["pt"], ms["lk"], ms["sf"], ms["ty"], ms["dn"],
                      now, now, now))
            stock_ids[ms["symbol"]] = sid

        # 确保有模拟账户
        acct = conn.execute("SELECT id FROM paper_account LIMIT 1").fetchone()
        if not acct:
            acct_id = str(uuid.uuid4())
            conn.execute("""
                INSERT INTO paper_account (id, initial_capital, current_capital,
                    total_trades, win_trades, total_pnl, max_drawdown, updated_at)
                VALUES (?, 100000, 98250, 8, 5, -1750, 3.2, ?)
            """, (acct_id, now))
        else:
            acct_id = acct['id']
            conn.execute("""
                UPDATE paper_account SET current_capital=98250, total_trades=8,
                    win_trades=5, total_pnl=-1750, max_drawdown=3.2, updated_at=?
                WHERE id=?
            """, (now, acct_id))

        # 清除旧模拟订单
        conn.execute("DELETE FROM paper_orders")

        # 持仓中的订单 (AAPL 盈利中, MSFT 盈利中)
        orders = [
            # AAPL: 买入170, 现价178.50, 盈利中
            (str(uuid.uuid4()), stock_ids["AAPL"], "AAPL", "long", "market",
             170.00, 58, 161.50, 195.50, "open", "2026-02-28T09:30:00", None, None, None, None, None, now),
            # MSFT: 买入400, 现价415.20, 盈利中
            (str(uuid.uuid4()), stock_ids["MSFT"], "MSFT", "long", "market",
             400.00, 25, 380.00, 460.00, "open", "2026-03-05T10:15:00", None, None, None, None, None, now),
            # 已平仓: TSLA 止损
            (str(uuid.uuid4()), stock_ids["TSLA"], "TSLA", "long", "market",
             260.00, 38, 247.00, 299.00, "closed", "2026-02-15T09:45:00",
             "2026-02-20T14:30:00", 247.00, "stop_loss", -494.00, -5.0, now),
            # 已平仓: NVDA 止盈
            (str(uuid.uuid4()), stock_ids["NVDA"], "NVDA", "long", "market",
             820.00, 12, 779.00, 943.00, "closed", "2026-02-10T10:00:00",
             "2026-03-01T15:00:00", 943.00, "take_profit", 1476.00, 15.0, now),
            # 已平仓: AMZN 手动平仓盈利
            (str(uuid.uuid4()), stock_ids["AMZN"], "AMZN", "long", "market",
             175.00, 57, 166.25, 201.25, "closed", "2026-01-20T09:30:00",
             "2026-02-05T14:00:00", 188.00, "manual", 741.00, 7.4, now),
            # 已平仓: META 止盈
            (str(uuid.uuid4()), stock_ids["META"], "META", "long", "market",
             480.00, 20, 456.00, 552.00, "closed", "2026-02-01T10:30:00",
             "2026-02-25T11:00:00", 552.00, "take_profit", 1440.00, 15.0, now),
            # 已平仓: GOOG 止损
            (str(uuid.uuid4()), stock_ids["GOOG"], "GOOG", "long", "market",
             165.00, 60, 156.75, 189.75, "closed", "2026-01-15T09:30:00",
             "2026-01-22T10:00:00", 156.75, "stop_loss", -495.00, -5.0, now),
            # 已平仓: AAPL 之前一笔止盈
            (str(uuid.uuid4()), stock_ids["AAPL"], "AAPL", "long", "market",
             165.00, 60, 156.75, 189.75, "closed", "2026-01-10T09:30:00",
             "2026-02-01T15:00:00", 182.00, "manual", 1020.00, 10.3, now),
        ]

        for o in orders:
            conn.execute("""
                INSERT INTO paper_orders (id, stock_id, symbol, direction, order_type,
                    price, quantity, stop_loss, take_profit, status, open_time,
                    close_time, close_price, close_reason, pnl, pnl_pct, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, o)

    return {"ok": True, "message": f"已生成 {len(mock_stocks)} 个品种和 {len(orders)} 条交易记录"}
