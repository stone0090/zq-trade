"""分析服务层 - 封装 core/ 分析引擎"""
import sys
import json
import threading
import time
from pathlib import Path
from datetime import datetime

# 确保项目根目录在路径中
_root = Path(__file__).resolve().parent.parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from core import analyze as core_analyze, fetch_kline, detect_market, get_stock_name
from core import run_full_analysis, AnalyzerConfig
from core.serializer import scorecard_to_dict, extract_grades

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

# matplotlib 非线程安全，加锁保护
_chart_lock = threading.Lock()

# ─── 全局分析进度追踪 ───
_progress_lock = threading.Lock()
_analysis_progress = {
    'running': False,
    'total': 0,
    'completed': 0,
    'current_symbol': None,
}

# ─── 停止分析标志 ───
_stop_analysis_flag = False


def get_progress() -> dict:
    """获取当前分析进度"""
    with _progress_lock:
        return dict(_analysis_progress)


def is_running() -> bool:
    """是否有分析在运行"""
    with _progress_lock:
        return _analysis_progress['running']


def stop_analysis():
    """请求停止分析"""
    global _stop_analysis_flag
    _stop_analysis_flag = True


def reset_stop_flag():
    """重置停止标志（分析开始前调用）"""
    global _stop_analysis_flag
    _stop_analysis_flag = False


def is_stop_requested() -> bool:
    """检查是否请求停止"""
    return _stop_analysis_flag


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

    df = fetch_kline(symbol=symbol, end_date=end_date, bars=300)
    if df is None or df.empty:
        raise ValueError(f"未能获取到 {symbol} 的有效数据")

    config = AnalyzerConfig()
    card = run_full_analysis(df, symbol=symbol, config=config, market=market)
    card.symbol_name = get_stock_name(symbol)
    card.market = market

    # 生成图表
    chart_path = None
    if chart_dir:
        from core.report.chart import _build_chart
        chart_dir_path = Path(chart_dir)
        chart_dir_path.mkdir(parents=True, exist_ok=True)
        filepath = str(chart_dir_path / f"{symbol}_{end_date or 'latest'}.png")

        with _chart_lock:
            fig = _build_chart(df, card)
            fig.savefig(filepath, dpi=150, bbox_inches='tight', facecolor='white')
            plt.close(fig)

        chart_path = filepath

    card_dict = scorecard_to_dict(card)
    grades = extract_grades(card_dict)

    conclusion = ''
    if card.conclusion_lines:
        conclusion = card.conclusion_lines[0] if card.conclusion_lines else ''

    # 数据最后时间（K线最新时间戳）
    last_data_time = str(df.index[-1])

    return {
        'score_card': card_dict,
        'grades': grades,
        'conclusion': conclusion,
        'position_size': card.position_size or '',
        'symbol_name': card.symbol_name or '',
        'market': market,
        'chart_path': chart_path,
        'last_data_time': last_data_time,
    }


def analyze_stocks_sync(stock_ids: list, db_path: str, chart_dir: str):
    """
    同步执行批量分析（在后台线程中调用）。
    按 stock_ids 列表逐个分析，更新全局进度。
    """
    import sqlite3
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")

    try:
        # 重置停止标志
        reset_stop_flag()
        
        # 获取待分析股票信息
        if not stock_ids:
            return
        placeholders = ','.join('?' * len(stock_ids))
        rows = conn.execute(
            f"SELECT id, symbol, end_date FROM stocks WHERE id IN ({placeholders})",
            stock_ids
        ).fetchall()

        with _progress_lock:
            _analysis_progress['running'] = True
            _analysis_progress['total'] = len(rows)
            _analysis_progress['completed'] = 0
            _analysis_progress['current_symbol'] = None

        # 重置股票状态
        for row in rows:
            conn.execute(
                "UPDATE stocks SET status='pending', error_message=NULL WHERE id=?",
                (row['id'],)
            )
        conn.commit()

        for i, row in enumerate(rows):
            # 检查是否请求停止
            if is_stop_requested():
                logger.info("分析被用户停止")
                break
            stock_id = row['id']
            symbol = row['symbol']
            end_date = row['end_date']

            if i > 0:
                time.sleep(2)

            with _progress_lock:
                _analysis_progress['current_symbol'] = symbol

            conn.execute(
                "UPDATE stocks SET status='analyzing' WHERE id=?",
                (stock_id,)
            )
            conn.commit()

            try:
                result = analyze_stock(symbol, end_date, chart_dir)

                # 名称获取失败时保留数据库中原有名称
                new_name = result['symbol_name']
                if not new_name:
                    existing = conn.execute(
                        "SELECT symbol_name FROM stocks WHERE id=?", (stock_id,)
                    ).fetchone()
                    if existing and existing[0]:
                        new_name = existing[0]

                conn.execute("""
                    UPDATE stocks SET
                        status='completed',
                        symbol_name=?, market=?,
                        score_card_json=?, chart_path=?,
                        dl_grade=?, pt_grade=?, lk_grade=?,
                        sf_grade=?, ty_grade=?, dn_grade=?,
                        conclusion=?, position_size=?,
                        analyzed_at=?, updated_at=?, kline_end_time=?
                    WHERE id=?
                """, (
                    new_name, result['market'],
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
                    result.get('last_data_time'),
                    stock_id,
                ))
                conn.commit()

            except Exception as e:
                conn.execute(
                    "UPDATE stocks SET status='error', error_message=? WHERE id=?",
                    (str(e), stock_id)
                )
                conn.commit()

            with _progress_lock:
                _analysis_progress['completed'] += 1

    finally:
        with _progress_lock:
            _analysis_progress['running'] = False
            _analysis_progress['current_symbol'] = None
        conn.close()
