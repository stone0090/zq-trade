"""股票生命周期状态机

6状态模型:
  pending  (待入库) → idle (在库中)  → watching (关注中)  → focused (重点关注)
  focused  → holding (持仓中) → idle

转移规则:
  pending → idle       : 人工确认
  idle → watching      : 扫描发现 DL=S, PT>=B, LK>=B, SF<=2nd
  idle → removed       : 人工移除
  watching → focused   : 监控发现 DL=S, PT>=A, LK>=A, SF=1st, TY>=A
  watching → idle      : 不满足关注条件
  watching → removed   : 人工移除
  focused → watching   : 不满足重点条件但仍满足关注条件
  focused → idle       : 不满足关注条件
  focused → holding    : DL=S, PT>=A, LK>=A, SF=1st, TY>=A, DN>=A，模拟下单
  focused → removed    : 人工移除
  holding → idle       : 平仓 (止损/止盈/手动)
  removed → idle       : 人工恢复

注意: 扫描/监控任务不会自动将品种移到 removed，只有人工操作或完全无数据才会。
"""
import logging
from datetime import datetime

from web.database import get_db

logger = logging.getLogger(__name__)

# 合法的状态转移
VALID_TRANSITIONS = {
    'none': ['pending', 'idle'],
    'pending': ['idle', 'removed'],
    'idle': ['watching', 'removed'],
    'watching': ['focused', 'idle', 'removed'],
    'focused': ['watching', 'holding', 'removed', 'idle'],
    'holding': ['idle'],
    'removed': ['pending'],  # 恢复只能到待入库
}

# 六维评级优先级 (用于比较)
_GRADE_ORDER = {'S': 4, 'A': 3, 'B': 2, 'C': 1, '待定': 0, None: -1, '': -1}


def _grade_gte(grade, threshold):
    """评级 >= 阈值"""
    return _GRADE_ORDER.get(grade, -1) >= _GRADE_ORDER.get(threshold, -1)


def transition_stock(stock_id: str, target_status: str, reason: str = "") -> dict:
    """执行状态转移，返回 {ok, message, old_status, new_status}"""
    with get_db() as conn:
        row = conn.execute(
            "SELECT id, symbol, watch_status FROM stocks WHERE id=?", (stock_id,)
        ).fetchone()
        if not row:
            return {"ok": False, "message": "股票不存在"}

        current = row['watch_status'] or 'none'
        if target_status not in VALID_TRANSITIONS.get(current, []):
            return {
                "ok": False,
                "message": f"不允许从 {current} 转移到 {target_status}"
            }

        now = datetime.now().isoformat()
        conn.execute(
            "UPDATE stocks SET watch_status=?, updated_at=? WHERE id=?",
            (target_status, now, stock_id)
        )
        logger.info(f"[状态机] {row['symbol']}: {current} → {target_status} ({reason})")

    return {
        "ok": True,
        "message": f"{current} → {target_status}",
        "old_status": current,
        "new_status": target_status,
    }


def batch_transition(stock_ids: list, target_status: str, reason: str = "") -> dict:
    """批量状态转移"""
    results = {"success": 0, "failed": 0, "errors": []}
    for sid in stock_ids:
        r = transition_stock(sid, target_status, reason)
        if r["ok"]:
            results["success"] += 1
        else:
            results["failed"] += 1
            results["errors"].append(f"{sid}: {r['message']}")
    return results


def meets_watching_criteria(stock: dict) -> bool:
    """检查是否满足从 idle 升级到 watching 的条件
    条件: DL=S, PT>=B, LK>=B, SF<=2nd (即 1st 或 2nd)
    """
    if stock.get('dl_grade') != 'S':
        return False
    if not _grade_gte(stock.get('pt_grade'), 'B'):
        return False
    if not _grade_gte(stock.get('lk_grade'), 'B'):
        return False
    if stock.get('sf_grade') not in ('1st', '2nd'):
        return False
    return True


def meets_focused_criteria(stock: dict) -> bool:
    """检查是否满足从 watching 升级到 focused 的条件
    条件: DL=S, PT>=A, LK>=A, SF=1st, TY>=A
    """
    if stock.get('dl_grade') != 'S':
        return False
    if not _grade_gte(stock.get('pt_grade'), 'A'):
        return False
    if not _grade_gte(stock.get('lk_grade'), 'A'):
        return False
    if stock.get('sf_grade') != '1st':
        return False
    if not _grade_gte(stock.get('ty_grade'), 'A'):
        return False
    return True


def meets_order_criteria(stock: dict) -> bool:
    """检查是否满足下单条件（从 focused 到 holding）
    条件: DL=S, PT>=A, LK>=A, SF=1st, TY>=A, DN>=A
    """
    if stock.get('dl_grade') != 'S':
        return False
    if not _grade_gte(stock.get('pt_grade'), 'A'):
        return False
    if not _grade_gte(stock.get('lk_grade'), 'A'):
        return False
    if stock.get('sf_grade') != '1st':
        return False
    if not _grade_gte(stock.get('ty_grade'), 'A'):
        return False
    if not _grade_gte(stock.get('dn_grade'), 'A'):
        return False
    return True


def is_deteriorated(stock: dict) -> bool:
    """检查形态是否严重恶化（应移除）
    条件: DL不是S，或PT和LK同时为C
    """
    if stock.get('dl_grade') not in ('S', None, '', '待定'):
        return True
    if stock.get('pt_grade') == 'C' and stock.get('lk_grade') == 'C':
        return True
    return False


def is_downgraded(stock: dict) -> bool:
    """检查 watching 是否应降级回 idle
    条件: 不满足 watching 条件 (DL!=S 或 PT<B 或 LK<B)
    """
    return not meets_watching_criteria(stock)


def get_stocks_by_watch_status(status: str) -> list:
    """获取指定监控状态的所有股票（纯算法评级，不关联人工标注）"""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT s.*
            FROM stocks s
            WHERE s.watch_status = ?
            ORDER BY s.updated_at DESC
        """, (status,)).fetchall()
    return [dict(r) for r in rows]


def get_effective_grades(stock: dict) -> dict:
    """获取有效评级（品种库使用纯算法评级）"""
    return {
        'dl_grade': stock.get('dl_grade'),
        'pt_grade': stock.get('pt_grade'),
        'lk_grade': stock.get('lk_grade'),
        'sf_grade': stock.get('sf_grade'),
        'ty_grade': stock.get('ty_grade'),
        'dn_grade': stock.get('dn_grade'),
    }
