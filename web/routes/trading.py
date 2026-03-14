"""模拟交易 API"""
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional

from web.services.trader import (
    get_account_summary, close_order, execute_paper_trade,
    get_open_positions, get_trade_history,
)
from web.database import get_db

router = APIRouter(prefix="/api/trading", tags=["trading"])


class ManualOrderReq(BaseModel):
    stock_id: str
    direction: str = "long"
    price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None


class CloseOrderReq(BaseModel):
    order_id: str
    close_price: Optional[float] = None
    reason: str = "manual"


@router.get("/summary")
def api_account_summary():
    """获取账户概览"""
    return get_account_summary()


@router.get("/positions")
def api_positions():
    """当前持仓"""
    return get_open_positions()


@router.get("/history")
def api_history(limit: int = Query(50)):
    """历史交易"""
    return get_trade_history(limit)


@router.post("/order")
def api_manual_order(req: ManualOrderReq):
    """手动下单"""
    with get_db() as conn:
        stock = conn.execute(
            "SELECT * FROM stocks WHERE id=?", (req.stock_id,)
        ).fetchone()
        if not stock:
            raise HTTPException(404, "股票不存在")
        stock_dict = dict(stock)

    if req.price:
        stock_dict['last_price'] = req.price

    result = execute_paper_trade(stock_dict)
    if not result:
        raise HTTPException(400, "下单失败：可能已达最大持仓或无有效价格")
    return result


@router.post("/close")
def api_close_order(req: CloseOrderReq):
    """平仓"""
    # 获取平仓价
    close_price = req.close_price
    if not close_price:
        with get_db() as conn:
            order = conn.execute(
                "SELECT stock_id FROM paper_orders WHERE id=?", (req.order_id,)
            ).fetchone()
            if order:
                stock = conn.execute(
                    "SELECT last_price FROM stocks WHERE id=?", (order['stock_id'],)
                ).fetchone()
                if stock and stock['last_price']:
                    close_price = stock['last_price']

    if not close_price:
        raise HTTPException(400, "请提供平仓价格")

    result = close_order(req.order_id, close_price, req.reason)
    if not result.get("ok"):
        raise HTTPException(400, result.get("message", "平仓失败"))
    return result


@router.post("/reset")
def api_reset_account():
    """重置模拟账户"""
    with get_db() as conn:
        conn.execute("DELETE FROM paper_orders")
        conn.execute("DELETE FROM paper_account")
        # 重置持仓状态
        conn.execute(
            "UPDATE stocks SET watch_status='idle' WHERE watch_status='holding'"
        )
    return {"ok": True, "message": "账户已重置"}
