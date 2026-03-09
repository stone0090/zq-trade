"""分析服务层 - 封装 src/ 分析引擎"""
import sys
import json
import threading
import time
import uuid
from pathlib import Path
from datetime import datetime
from dataclasses import asdict
from enum import Enum

# 确保 src/ 在路径中
_root = Path(__file__).resolve().parent.parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from src.data.fetcher import fetch_kline_smart, get_stock_name, detect_market
from src.analyzer.scorer import run_full_analysis
from src.analyzer.base import AnalyzerConfig, GradeScore, ReleaseLevel

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

# matplotlib 非线程安全，加锁保护
_chart_lock = threading.Lock()


def _serialize(obj):
    """递归序列化 dataclass / enum / datetime / numpy"""
    if obj is None:
        return None
    if isinstance(obj, Enum):
        return obj.name if isinstance(obj, GradeScore) else str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, (bool,)):
        return obj
    # numpy 类型处理
    try:
        import numpy as np
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            return float(obj)
        if isinstance(obj, np.bool_):
            return bool(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
    except ImportError:
        pass
    if isinstance(obj, (list, tuple)):
        return [_serialize(v) for v in obj]
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    if hasattr(obj, '__dataclass_fields__'):
        return {k: _serialize(v) for k, v in asdict(obj).items()}
    return obj


def scorecard_to_dict(card) -> dict:
    """将 ScoreCard 转为可 JSON 序列化的 dict"""
    d = {}
    for field_name in card.__dataclass_fields__:
        val = getattr(card, field_name)
        d[field_name] = _serialize(val)
    return d


def extract_grades(card_dict: dict) -> dict:
    """从 ScoreCard dict 提取各维度 grade 字符串"""
    grades = {}

    dl = card_dict.get('dl_result')
    if dl:
        score = dl.get('score')
        grades['dl_grade'] = score if score else None
    else:
        grades['dl_grade'] = None

    pt = card_dict.get('pt_result')
    if pt:
        grades['pt_grade'] = pt.get('score')
    else:
        grades['pt_grade'] = None

    lk = card_dict.get('lk_result')
    if lk:
        grades['lk_grade'] = lk.get('score')
    else:
        grades['lk_grade'] = None

    sf = card_dict.get('sf_result')
    if sf:
        score_val = sf.get('score')
        grades['sf_grade'] = str(score_val) if score_val else None
    else:
        grades['sf_grade'] = None

    ty = card_dict.get('ty_result')
    if ty:
        if ty.get('pending'):
            grades['ty_grade'] = '待定'
        else:
            grades['ty_grade'] = ty.get('score')
    else:
        grades['ty_grade'] = None

    dn = card_dict.get('dn_result')
    if dn:
        if dn.get('pending'):
            grades['dn_grade'] = '待定'
        else:
            grades['dn_grade'] = dn.get('score')
    else:
        grades['dn_grade'] = None

    return grades


def analyze_stock(symbol: str, end_date: str = None, chart_dir: str = None) -> dict:
    """
    分析单只股票，返回完整结果。

    Returns:
        {
            'score_card': dict,
            'grades': {dl_grade, pt_grade, ...},
            'conclusion': str,
            'position_size': str,
            'symbol_name': str,
            'market': str,
            'chart_path': str or None,
        }
    """
    market = detect_market(symbol)

    df = fetch_kline_smart(symbol=symbol, end_date=end_date, bars=300)
    if df is None or df.empty:
        raise ValueError(f"未能获取到 {symbol} 的有效数据")

    config = AnalyzerConfig()
    card = run_full_analysis(df, symbol=symbol, config=config)
    card.symbol_name = get_stock_name(symbol)
    card.market = market

    # 生成图表
    chart_path = None
    if chart_dir:
        from src.report.charger import _build_chart
        chart_dir_path = Path(chart_dir)
        chart_dir_path.mkdir(parents=True, exist_ok=True)
        filepath = str(chart_dir_path / f"{symbol}.png")

        with _chart_lock:
            fig = _build_chart(df, card)
            fig.savefig(filepath, dpi=150, bbox_inches='tight', facecolor='white')
            plt.close(fig)

        chart_path = filepath

    card_dict = scorecard_to_dict(card)
    grades = extract_grades(card_dict)

    conclusion = ''
    if card.conclusion_lines:
        # 第一行是极简结论
        conclusion = card.conclusion_lines[0] if card.conclusion_lines else ''

    return {
        'score_card': card_dict,
        'grades': grades,
        'conclusion': conclusion,
        'position_size': card.position_size or '',
        'symbol_name': card.symbol_name or '',
        'market': market,
        'chart_path': chart_path,
    }


def analyze_batch_sync(batch_id: str, db_path: str, chart_base_dir: str):
    """
    同步执行批量分析（在后台线程中调用）。
    """
    import sqlite3
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")

    try:
        # 更新批次状态
        conn.execute("UPDATE batches SET status='running' WHERE id=?", (batch_id,))
        conn.commit()

        # 获取待分析股票
        rows = conn.execute(
            "SELECT id, symbol, end_date FROM stocks WHERE batch_id=? AND status='pending'",
            (batch_id,)
        ).fetchall()

        chart_dir = str(Path(chart_base_dir) / batch_id)

        for i, row in enumerate(rows):
            stock_id = row['id']
            symbol = row['symbol']
            end_date = row['end_date']

            if i > 0:
                time.sleep(2)

            conn.execute(
                "UPDATE stocks SET status='analyzing' WHERE id=?",
                (stock_id,)
            )
            conn.commit()

            try:
                result = analyze_stock(symbol, end_date, chart_dir)

                conn.execute("""
                    UPDATE stocks SET
                        status='completed',
                        symbol_name=?, market=?,
                        score_card_json=?, chart_path=?,
                        dl_grade=?, pt_grade=?, lk_grade=?,
                        sf_grade=?, ty_grade=?, dn_grade=?,
                        conclusion=?, position_size=?,
                        analyzed_at=?
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
                    stock_id,
                ))

                # 更新批次进度
                conn.execute(
                    "UPDATE batches SET completed_count = completed_count + 1 WHERE id=?",
                    (batch_id,)
                )
                conn.commit()

            except Exception as e:
                conn.execute(
                    "UPDATE stocks SET status='error', error_message=? WHERE id=?",
                    (str(e), stock_id)
                )
                conn.execute(
                    "UPDATE batches SET completed_count = completed_count + 1 WHERE id=?",
                    (batch_id,)
                )
                conn.commit()

        conn.execute("UPDATE batches SET status='completed' WHERE id=?", (batch_id,))
        conn.commit()

    except Exception as e:
        conn.execute(
            "UPDATE batches SET status='failed' WHERE id=?",
            (batch_id,)
        )
        conn.commit()
    finally:
        conn.close()
