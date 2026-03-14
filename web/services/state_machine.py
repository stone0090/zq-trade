"""股票生命周期状态机

6状态模型:
  pending  (待入库) → idle (在库中)  → watching (关注中)  → focused (重点关注)
  focused  → holding (持仓中) → idle
  idle/watching → removed (已移除)

转移规则:
  pending → idle       : 人工确认
  idle → watching      : 每日扫描发现形态部分满足
  idle → removed       : 形态严重恶化或人工移除
  watching → focused   : 形态改善 (DL=S, PT>=B, LK>=B, SF=1st, TY/DN待触发)
  watching → idle      : 形态退化但不严重，回在库中
  watching → removed   : 形态严重恶化
  focused → watching   : TY走坏但LK/PT/SF仍达标
  focused → holding    : 六维全部达标，模拟下单
  holding → idle       : 平仓 (止损/止盈/手动)
  removed → idle       : 人工恢复
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
    'focused': ['watching', 'holding', 'removed'],
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
    条件: DL=S，且至少有2个其他维度 >= B
    """
    dl = stock.get('dl_grade')
    if dl != 'S':
        return False

    other_grades = [
        stock.get('pt_grade'),
        stock.get('lk_grade'),
        stock.get('sf_grade'),
        stock.get('ty_grade'),
    ]
    good_count = sum(1 for g in other_grades if _grade_gte(g, 'B') or g in ('1st', '2nd'))
    return good_count >= 2


def meets_focused_criteria(stock: dict) -> bool:
    """检查是否满足从 watching 升级到 focused 的条件
    条件: DL=S, PT>=B, LK>=B, SF=1st, TY/DN 可以待定
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


def meets_order_criteria(stock: dict) -> bool:
    """检查是否满足下单条件（从 focused 到 holding）
    条件: DL=S, PT>=A, LK>=A, SF=1st, TY>=B, DN>=B
    """
    if stock.get('dl_grade') != 'S':
        return False
    if not _grade_gte(stock.get('pt_grade'), 'A'):
        return False
    if not _grade_gte(stock.get('lk_grade'), 'A'):
        return False
    if stock.get('sf_grade') != '1st':
        return False
    if not _grade_gte(stock.get('ty_grade'), 'B'):
        return False
    if not _grade_gte(stock.get('dn_grade'), 'B'):
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
    条件: 不满足 watching 条件但也没严重恶化
    """
    return not meets_watching_criteria(stock) and not is_deteriorated(stock)


def get_stocks_by_watch_status(status: str) -> list:
    """获取指定监控状态的所有股票"""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT s.*, l.dl_grade as label_dl, l.pt_grade as label_pt,
                   l.lk_grade as label_lk, l.sf_grade as label_sf,
                   l.ty_grade as label_ty, l.dn_grade as label_dn
            FROM stocks s
            LEFT JOIN labels l ON l.stock_id = s.id
            WHERE s.watch_status = ?
            ORDER BY s.updated_at DESC
        """, (status,)).fetchall()
    return [dict(r) for r in rows]


def get_effective_grades(stock: dict) -> dict:
    """获取有效评级（优先用系统分析结果，其次用人工标注）"""
    return {
        'dl_grade': stock.get('dl_grade') or stock.get('label_dl'),
        'pt_grade': stock.get('pt_grade') or stock.get('label_pt'),
        'lk_grade': stock.get('lk_grade') or stock.get('label_lk'),
        'sf_grade': stock.get('sf_grade') or stock.get('label_sf'),
        'ty_grade': stock.get('ty_grade') or stock.get('label_ty'),
        'dn_grade': stock.get('dn_grade') or stock.get('label_dn'),
    }
