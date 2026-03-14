"""监控引擎 — 核心监控逻辑"""
import logging
import json
import time
from datetime import datetime
from typing import Optional

from web.database import get_db
from web.services.state_machine import (
    get_stocks_by_watch_status, get_effective_grades,
    meets_watching_criteria, meets_focused_criteria, meets_order_criteria,
    is_deteriorated, is_downgraded, transition_stock,
)
from web.services.notifier import get_notifier

logger = logging.getLogger(__name__)


def refresh_stock_data(stocks) -> int:
    """刷新股票最新价格和K线数据（在扫描前调用）"""
    updated = 0
    for i, stock in enumerate(stocks):
        if i > 0:
            time.sleep(1)  # 每只股票间隔1秒，避免API限流
        sym = stock['symbol']
        market = stock.get('market', 'us')
        try:
            price = _fetch_latest_price(sym, market)
            if price and price > 0:
                with get_db() as conn:
                    conn.execute(
                        "UPDATE stocks SET last_price=?, last_price_time=? WHERE id=?",
                        (price, datetime.now().isoformat(), stock['id'])
                    )
        except Exception as e:
            logger.warning(f"获取 {sym} 最新价失败: {e}")
        try:
            from core import fetch_kline
            fetch_kline(sym, bars=100)
            updated += 1
        except Exception as e:
            logger.warning(f"刷新 {sym} K线失败: {e}")
    return updated


def run_daily_scan() -> str:
    """每日品种扫描：检查 idle 池中的品种是否满足 watching 条件"""
    stocks = get_stocks_by_watch_status('idle')
    upgraded = 0
    removed = 0

    for stock in stocks:
        grades = get_effective_grades(stock)

        if is_deteriorated(grades):
            transition_stock(stock['id'], 'removed', '每日扫描-形态严重恶化')
            removed += 1
            continue

        if meets_watching_criteria(grades):
            transition_stock(stock['id'], 'watching', '每日扫描-形态部分满足')
            upgraded += 1

    summary = f"扫描 {len(stocks)} 只idle品种，{upgraded} 只升级到watching，{removed} 只移除"
    logger.info(f"[每日扫描] {summary}")

    # 发送通知
    if upgraded > 0 or removed > 0:
        notifier = get_notifier()
        if notifier:
            notifier.send_text(f"[每日扫描] {summary}")

    return summary


def run_watch_monitor() -> str:
    """关注中监控（每1小时）：检查 watching 品种的升降级"""
    stocks = get_stocks_by_watch_status('watching')
    upgraded = 0
    downgraded = 0
    removed = 0

    for stock in stocks:
        grades = get_effective_grades(stock)

        if is_deteriorated(grades):
            transition_stock(stock['id'], 'removed', '扫描监控-形态严重恶化')
            removed += 1
            continue

        if meets_focused_criteria(grades):
            transition_stock(stock['id'], 'focused', '扫描监控-形态改善升级')
            upgraded += 1
            _notify_upgrade(stock, 'watching', 'focused')
            continue

        if is_downgraded(grades):
            transition_stock(stock['id'], 'idle', '扫描监控-形态退化')
            downgraded += 1

    summary = (f"扫描 {len(stocks)} 只watching品种，"
               f"{upgraded} 只升级，{downgraded} 只降级，{removed} 只移除")
    logger.info(f"[关注中监控] {summary}")
    return summary


def run_focus_monitor() -> str:
    """重点列表监控（每5分钟）：检查 focused 品种的DN触发和TY走坏"""
    stocks = get_stocks_by_watch_status('focused')
    triggered = 0
    downgraded = 0

    for stock in stocks:
        grades = get_effective_grades(stock)

        # 检查是否满足下单条件
        if meets_order_criteria(grades):
            # 执行模拟下单
            try:
                from web.services.trader import execute_paper_trade
                result = execute_paper_trade(stock)
                if result and result.get('ok'):
                    transition_stock(stock['id'], 'holding', 'DN触发-模拟下单')
                    triggered += 1
                    _notify_trade(stock, result)
            except Exception as e:
                logger.error(f"模拟下单失败 {stock['symbol']}: {e}")
            continue

        # 检查 TY 是否走坏 (从 focused 降到 watching)
        ty = grades.get('ty_grade')
        if ty == 'C' and meets_watching_criteria(grades):
            transition_stock(stock['id'], 'watching', '重点监控-TY走坏')
            downgraded += 1
            _notify_downgrade(stock, 'focused', 'watching')

    summary = (f"扫描 {len(stocks)} 只focused品种，"
               f"{triggered} 只触发下单，{downgraded} 只降级")
    logger.info(f"[重点列表监控] {summary}")
    return summary


def check_holding_positions() -> str:
    """检查持仓中品种的止损止盈"""
    stocks = get_stocks_by_watch_status('holding')
    closed = 0

    for stock in stocks:
        try:
            from web.services.trader import check_stop_loss_take_profit
            result = check_stop_loss_take_profit(stock)
            if result and result.get('closed'):
                # transition_stock 已在 close_order() 内部调用
                closed += 1
        except Exception as e:
            logger.error(f"检查持仓失败 {stock['symbol']}: {e}")

    summary = f"检查 {len(stocks)} 只持仓，{closed} 只平仓"
    logger.info(f"[持仓检查] {summary}")
    return summary


def refresh_latest_prices(statuses=None) -> str:
    """刷新最新价格"""
    if statuses is None:
        statuses = ['watching', 'focused', 'holding']

    total = 0
    updated = 0

    for status in statuses:
        stocks = get_stocks_by_watch_status(status)
        total += len(stocks)

        for i, stock in enumerate(stocks):
            if i > 0:
                time.sleep(0.5)  # 每只股票间隔0.5秒，避免API限流
            try:
                price = _fetch_latest_price(stock['symbol'], stock['market'])
                if price and price > 0:
                    with get_db() as conn:
                        conn.execute(
                            "UPDATE stocks SET last_price=?, last_price_time=? WHERE id=?",
                            (price, datetime.now().isoformat(), stock['id'])
                        )
                    updated += 1
            except Exception as e:
                logger.warning(f"获取 {stock['symbol']} 最新价失败: {e}")

    summary = f"刷新 {total} 只品种价格，成功 {updated}"
    logger.info(f"[价格刷新] {summary}")
    return summary


def _fetch_latest_price(symbol: str, market: str) -> Optional[float]:
    """获取最新价格（通过 Yahoo Finance）"""
    try:
        import yfinance as yf
        if market == 'hk':
            yahoo_sym = f"{int(symbol):04d}.HK"
        elif market == 'cn':
            suffix = '.SS' if symbol.startswith('6') else '.SZ'
            yahoo_sym = f"{symbol}{suffix}"
        else:
            yahoo_sym = symbol

        ticker = yf.Ticker(yahoo_sym)
        info = ticker.fast_info
        price = getattr(info, 'last_price', None) or getattr(info, 'previous_close', None)
        return float(price) if price else None
    except Exception as e:
        logger.debug(f"yfinance 获取 {symbol} 价格失败: {e}")
        return None


def _notify_upgrade(stock: dict, from_status: str, to_status: str):
    """发送升级通知"""
    notifier = get_notifier()
    if notifier:
        notifier.send_card(
            f"品种升级: {stock['symbol']}",
            {
                "品种": f"{stock['symbol']} {stock.get('symbol_name', '')}",
                "状态变更": f"{from_status} → {to_status}",
                "六维评级": _format_grades(stock),
            }
        )


def _notify_downgrade(stock: dict, from_status: str, to_status: str):
    """发送降级通知"""
    notifier = get_notifier()
    if notifier:
        notifier.send_card(
            f"品种降级: {stock['symbol']}",
            {
                "品种": f"{stock['symbol']} {stock.get('symbol_name', '')}",
                "状态变更": f"{from_status} → {to_status}",
                "六维评级": _format_grades(stock),
            }
        )


def _notify_trade(stock: dict, trade_result: dict):
    """发送交易信号通知"""
    notifier = get_notifier()
    if notifier:
        notifier.send_card(
            f"交易信号: {stock['symbol']}",
            {
                "品种": f"{stock['symbol']} {stock.get('symbol_name', '')}",
                "方向": trade_result.get('direction', 'long'),
                "入场价": str(trade_result.get('price', '')),
                "止损价": str(trade_result.get('stop_loss', '')),
                "数量": str(trade_result.get('quantity', '')),
                "六维评级": _format_grades(stock),
            }
        )


def _format_grades(stock: dict) -> str:
    """格式化六维评级为字符串"""
    grades = get_effective_grades(stock) if 'label_dl' in stock else stock
    parts = []
    for dim in ['dl_grade', 'pt_grade', 'lk_grade', 'sf_grade', 'ty_grade', 'dn_grade']:
        g = grades.get(dim) or '-'
        parts.append(g)
    return '/'.join(parts)
