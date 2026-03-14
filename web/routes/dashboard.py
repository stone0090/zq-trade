"""仪表盘 API"""
from fastapi import APIRouter
from web.database import get_db

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/summary")
def dashboard_summary():
    """仪表盘概览数据"""
    with get_db() as conn:
        # 修复脏数据: holding 但无 open 订单的股票回退到 idle
        orphaned = conn.execute("""
            SELECT s.id FROM stocks s
            WHERE s.watch_status = 'holding'
              AND NOT EXISTS (
                SELECT 1 FROM paper_orders po
                WHERE po.stock_id = s.id AND po.status = 'open'
              )
        """).fetchall()
        if orphaned:
            ids = [r['id'] for r in orphaned]
            conn.execute(
                f"UPDATE stocks SET watch_status='idle', updated_at=datetime('now') "
                f"WHERE id IN ({','.join('?' * len(ids))})",
                ids
            )

        # 各状态品种数
        status_rows = conn.execute("""
            SELECT watch_status, COUNT(*) as cnt
            FROM stocks WHERE watch_status != 'none'
            GROUP BY watch_status
        """).fetchall()
        status_counts = {r['watch_status']: r['cnt'] for r in status_rows}

        # 账户信息
        account = conn.execute("SELECT * FROM paper_account LIMIT 1").fetchone()
        account_data = dict(account) if account else {
            "initial_capital": 100000, "current_capital": 100000,
            "total_trades": 0, "win_trades": 0, "total_pnl": 0, "max_drawdown": 0
        }

        # 当前持仓数
        open_count = conn.execute(
            "SELECT COUNT(*) as cnt FROM paper_orders WHERE status='open'"
        ).fetchone()['cnt']

        # 盈亏比
        avg_win = conn.execute(
            "SELECT AVG(pnl) as v FROM paper_orders WHERE status='closed' AND pnl > 0"
        ).fetchone()
        avg_loss = conn.execute(
            "SELECT AVG(ABS(pnl)) as v FROM paper_orders WHERE status='closed' AND pnl < 0"
        ).fetchone()
        aw = avg_win['v'] if avg_win and avg_win['v'] else 0
        al = avg_loss['v'] if avg_loss and avg_loss['v'] else 1
        profit_factor = round(aw / al, 2) if al > 0 else 0

        # 最近状态变更日志 (用 job_logs 代替)
        recent_logs = conn.execute(
            """SELECT job_name, started_at, status, result_summary
               FROM job_logs ORDER BY started_at DESC LIMIT 10"""
        ).fetchall()

        # 最近通知
        recent_notifs = conn.execute(
            """SELECT type, title, status, created_at
               FROM notifications ORDER BY created_at DESC LIMIT 10"""
        ).fetchall()

    win_rate = (account_data['win_trades'] / account_data['total_trades'] * 100
                if account_data['total_trades'] > 0 else 0)

    return {
        "counts": {
            "focused": status_counts.get("focused", 0),
            "watching": status_counts.get("watching", 0),
            "holding": status_counts.get("holding", 0),
            "idle": status_counts.get("idle", 0),
            "pending": status_counts.get("pending", 0),
        },
        "account": {
            "current_capital": account_data['current_capital'],
            "total_pnl": account_data['total_pnl'],
            "total_trades": account_data['total_trades'],
            "win_rate": round(win_rate, 1),
            "profit_factor": profit_factor,
            "max_drawdown": account_data['max_drawdown'],
            "open_positions": open_count,
        },
        "recent_logs": [dict(r) for r in recent_logs],
        "recent_notifications": [dict(r) for r in recent_notifs],
    }
