"""模拟交易引擎"""
import uuid
import logging
from datetime import datetime
from typing import Optional

from web.database import get_db
from web.services.notifier import get_notifier
from web.services.state_machine import transition_stock

logger = logging.getLogger(__name__)


def _get_account():
    """获取或创建模拟账户"""
    with get_db() as conn:
        row = conn.execute("SELECT * FROM paper_account LIMIT 1").fetchone()
        if row:
            return dict(row)

        account_id = str(uuid.uuid4())
        now = datetime.now().isoformat()

        # 从设置读取初始资金
        cap_row = conn.execute(
            "SELECT value FROM settings WHERE key='initial_capital'"
        ).fetchone()
        initial = float(cap_row['value']) if cap_row and cap_row['value'] else 100000.0

        conn.execute(
            """INSERT INTO paper_account
               (id, initial_capital, current_capital, total_trades, win_trades,
                total_pnl, max_drawdown, updated_at)
               VALUES (?, ?, ?, 0, 0, 0, 0, ?)""",
            (account_id, initial, initial, now)
        )
        return {
            "id": account_id,
            "initial_capital": initial,
            "current_capital": initial,
            "total_trades": 0,
            "win_trades": 0,
            "total_pnl": 0.0,
            "max_drawdown": 0.0,
            "updated_at": now,
        }


def get_account_summary() -> dict:
    """获取账户概览"""
    account = _get_account()

    with get_db() as conn:
        # 当前持仓
        open_orders = conn.execute(
            "SELECT * FROM paper_orders WHERE status='open' ORDER BY open_time DESC"
        ).fetchall()

        # 历史订单
        closed_orders = conn.execute(
            "SELECT * FROM paper_orders WHERE status='closed' ORDER BY close_time DESC LIMIT 50"
        ).fetchall()

    # 计算持仓浮动盈亏
    unrealized_pnl = 0.0
    positions = []
    for order in open_orders:
        o = dict(order)
        # 获取最新价
        with get_db() as conn:
            stock = conn.execute(
                "SELECT last_price FROM stocks WHERE id=?", (o['stock_id'],)
            ).fetchone()
        current_price = stock['last_price'] if stock and stock['last_price'] else o['price']
        if o['direction'] == 'long':
            pnl = (current_price - o['price']) * o['quantity']
            pnl_pct = (current_price - o['price']) / o['price'] * 100 if o['price'] else 0
        else:
            pnl = (o['price'] - current_price) * o['quantity']
            pnl_pct = (o['price'] - current_price) / o['price'] * 100 if o['price'] else 0
        o['current_price'] = current_price
        o['unrealized_pnl'] = round(pnl, 2)
        o['unrealized_pnl_pct'] = round(pnl_pct, 2)
        unrealized_pnl += pnl
        positions.append(o)

    win_rate = (account['win_trades'] / account['total_trades'] * 100
                if account['total_trades'] > 0 else 0)

    # 计算盈亏比
    with get_db() as conn:
        avg_win = conn.execute(
            "SELECT AVG(pnl) as avg FROM paper_orders WHERE status='closed' AND pnl > 0"
        ).fetchone()
        avg_loss = conn.execute(
            "SELECT AVG(ABS(pnl)) as avg FROM paper_orders WHERE status='closed' AND pnl < 0"
        ).fetchone()
    avg_w = avg_win['avg'] if avg_win and avg_win['avg'] else 0
    avg_l = avg_loss['avg'] if avg_loss and avg_loss['avg'] else 1
    profit_factor = round(avg_w / avg_l, 2) if avg_l > 0 else 0

    return {
        "account": account,
        "positions": positions,
        "history": [dict(o) for o in closed_orders],
        "unrealized_pnl": round(unrealized_pnl, 2),
        "win_rate": round(win_rate, 1),
        "profit_factor": profit_factor,
        "open_count": len(positions),
        "max_positions": 5,
    }


def execute_paper_trade(stock: dict) -> Optional[dict]:
    """执行模拟下单

    使用 1R 仓位管理:
    - 1R = 账户净值的 1%
    - 止损幅度决定数量: quantity = 1R / (entry - stop_loss)
    """
    account = _get_account()

    # 检查持仓数
    with get_db() as conn:
        open_count = conn.execute(
            "SELECT COUNT(*) as cnt FROM paper_orders WHERE status='open'"
        ).fetchone()['cnt']

    # 从设置读取最大持仓数
    with get_db() as conn:
        max_row = conn.execute(
            "SELECT value FROM settings WHERE key='max_positions'"
        ).fetchone()
    max_positions = int(max_row['value']) if max_row and max_row['value'] else 5

    if open_count >= max_positions:
        logger.warning(f"已达最大持仓数 {max_positions}，跳过 {stock['symbol']}")
        return None

    # 确定入场价和止损价
    entry_price = stock.get('last_price')
    if not entry_price or entry_price <= 0:
        logger.warning(f"{stock['symbol']} 无最新价，无法下单")
        return None

    # 从设置读取风险参数
    with get_db() as conn:
        risk_row = conn.execute(
            "SELECT value FROM settings WHERE key='risk_per_trade'"
        ).fetchone()
    risk_pct = float(risk_row['value']) if risk_row and risk_row['value'] else 1.0

    # 止损: 使用 ATR 或固定百分比
    stop_loss_pct = 0.05  # 默认5%止损
    stop_loss = round(entry_price * (1 - stop_loss_pct), 2)

    # 1R = 账户的 risk_pct%
    one_r = account['current_capital'] * (risk_pct / 100)
    risk_per_share = entry_price - stop_loss
    if risk_per_share <= 0:
        logger.warning(f"{stock['symbol']} 止损价异常")
        return None

    quantity = max(1, int(one_r / risk_per_share))

    # 止盈: 3R
    take_profit = round(entry_price + risk_per_share * 3, 2)

    now = datetime.now().isoformat()
    order_id = str(uuid.uuid4())

    with get_db() as conn:
        conn.execute(
            """INSERT INTO paper_orders
               (id, stock_id, symbol, direction, order_type, price, quantity,
                stop_loss, take_profit, status, open_time, created_at)
               VALUES (?, ?, ?, 'long', 'market', ?, ?, ?, ?, 'open', ?, ?)""",
            (order_id, stock['id'], stock['symbol'], entry_price, quantity,
             stop_loss, take_profit, now, now)
        )

    logger.info(f"[模拟下单] {stock['symbol']}: 买入 {quantity}股 @ {entry_price}, "
                f"止损 {stop_loss}, 止盈 {take_profit}")

    return {
        "ok": True,
        "order_id": order_id,
        "symbol": stock['symbol'],
        "direction": "long",
        "price": entry_price,
        "quantity": quantity,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
    }


def check_stop_loss_take_profit(stock: dict) -> Optional[dict]:
    """检查持仓的止损止盈"""
    current_price = stock.get('last_price')
    if not current_price:
        return None

    with get_db() as conn:
        orders = conn.execute(
            "SELECT * FROM paper_orders WHERE stock_id=? AND status='open'",
            (stock['id'],)
        ).fetchall()

    for order in orders:
        o = dict(order)
        reason = None

        if o['direction'] == 'long':
            if o['stop_loss'] and current_price <= o['stop_loss']:
                reason = 'stop_loss'
            elif o['take_profit'] and current_price >= o['take_profit']:
                reason = 'take_profit'
        else:
            if o['stop_loss'] and current_price >= o['stop_loss']:
                reason = 'stop_loss'
            elif o['take_profit'] and current_price <= o['take_profit']:
                reason = 'take_profit'

        if reason:
            close_order(o['id'], current_price, reason)
            return {"closed": True, "reason": reason, "order_id": o['id']}

    return {"closed": False}


def close_order(order_id: str, close_price: float, reason: str = "manual") -> dict:
    """平仓"""
    now = datetime.now().isoformat()

    with get_db() as conn:
        order = conn.execute(
            "SELECT * FROM paper_orders WHERE id=?", (order_id,)
        ).fetchone()
        if not order:
            return {"ok": False, "message": "订单不存在"}
        if order['status'] != 'open':
            return {"ok": False, "message": "订单已关闭"}

        o = dict(order)
        if o['direction'] == 'long':
            pnl = (close_price - o['price']) * o['quantity']
            pnl_pct = (close_price - o['price']) / o['price'] * 100
        else:
            pnl = (o['price'] - close_price) * o['quantity']
            pnl_pct = (o['price'] - close_price) / o['price'] * 100

        pnl = round(pnl, 2)
        pnl_pct = round(pnl_pct, 2)

        conn.execute(
            """UPDATE paper_orders
               SET status='closed', close_time=?, close_price=?,
                   close_reason=?, pnl=?, pnl_pct=?
               WHERE id=?""",
            (now, close_price, reason, pnl, pnl_pct, order_id)
        )

        # 更新账户
        account = conn.execute("SELECT * FROM paper_account LIMIT 1").fetchone()
        if account:
            new_capital = account['current_capital'] + pnl
            new_total = account['total_trades'] + 1
            new_wins = account['win_trades'] + (1 if pnl > 0 else 0)
            new_total_pnl = account['total_pnl'] + pnl
            # 计算最大回撤
            peak = max(account['initial_capital'], account['current_capital'])
            drawdown = (peak - new_capital) / peak * 100 if peak > 0 else 0
            max_dd = max(account['max_drawdown'], drawdown)

            conn.execute(
                """UPDATE paper_account
                   SET current_capital=?, total_trades=?, win_trades=?,
                       total_pnl=?, max_drawdown=?, updated_at=?
                   WHERE id=?""",
                (new_capital, new_total, new_wins, new_total_pnl,
                 round(max_dd, 2), now, account['id'])
            )

    # 平仓后将股票状态从 holding 转回 idle
    transition_stock(o['stock_id'], 'idle', f"平仓: {reason}")

    # 发送通知
    notifier = get_notifier()
    if notifier:
        emoji = "+" if pnl > 0 else ""
        notifier.send_card(
            f"平仓通知: {o['symbol']}",
            {
                "品种": o['symbol'],
                "方向": o['direction'],
                "开仓价": str(o['price']),
                "平仓价": str(close_price),
                "盈亏": f"{emoji}{pnl} ({emoji}{pnl_pct}%)",
                "原因": reason,
            }
        )

    logger.info(f"[平仓] {o['symbol']}: {reason}, PnL={pnl} ({pnl_pct}%)")
    return {"ok": True, "pnl": pnl, "pnl_pct": pnl_pct, "reason": reason}


def get_open_positions() -> list:
    """获取当前持仓"""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT po.*, s.symbol_name, s.last_price, s.market
               FROM paper_orders po
               JOIN stocks s ON s.id = po.stock_id
               WHERE po.status = 'open'
               ORDER BY po.open_time DESC"""
        ).fetchall()
    return [dict(r) for r in rows]


def get_trade_history(limit: int = 50) -> list:
    """获取历史交易"""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT po.*, s.symbol_name, s.market
               FROM paper_orders po
               JOIN stocks s ON s.id = po.stock_id
               WHERE po.status = 'closed'
               ORDER BY po.close_time DESC
               LIMIT ?""",
            (limit,)
        ).fetchall()
    return [dict(r) for r in rows]
