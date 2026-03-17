"""监控引擎 — 核心监控逻辑"""
import logging
import json
import time
import traceback
from datetime import datetime
from typing import Optional

from web.database import get_db
from web.services.state_machine import (
    get_stocks_by_watch_status, get_effective_grades,
    meets_watching_criteria, meets_focused_criteria, meets_order_criteria,
    transition_stock,
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
            logger.error(f"获取 {sym} 最新价失败: {e}\n{traceback.format_exc()}")
        try:
            from core import fetch_kline
            fetch_kline(sym, bars=100)
            updated += 1
        except Exception as e:
            logger.error(f"刷新 {sym} K线失败: {e}\n{traceback.format_exc()}")
    return updated


def run_daily_scan() -> str:
    """每周品种扫描：
    1. 清理完全无数据的品种（无名称+无评级+无价格）→ 自动移除
    2. 检查 idle 池中的品种是否满足 watching 条件 (DL=S, PT>=B, LK>=B, SF<=2nd)
    """
    stocks = get_stocks_by_watch_status('idle')
    upgraded = 0
    removed = 0

    for stock in stocks:
        # 完全无数据（无名称 + 无评级 + 无价格）→ 自动移除
        if (not stock.get('symbol_name')
                and not stock.get('last_price')
                and not stock.get('dl_grade')
                and not stock.get('pt_grade')
                and not stock.get('lk_grade')):
            transition_stock(stock['id'], 'removed', '完全无数据-自动移除')
            removed += 1
            logger.info(f"[每日扫描] {stock['symbol']} 完全无数据，移除")
            continue

        grades = get_effective_grades(stock)

        if meets_watching_criteria(grades):
            transition_stock(stock['id'], 'watching', '每日扫描-满足关注条件')
            upgraded += 1
            logger.info(f"[每日扫描] {stock['symbol']} 升级到watching grades={grades}")

    summary = f"扫描 {len(stocks)} 只idle品种，{upgraded} 只升级到watching，{removed} 只数据失败移除"
    logger.info(f"[每日扫描] {summary}")

    if upgraded > 0 or removed > 0:
        notifier = get_notifier()
        if notifier:
            notifier.send_text(f"[每日扫描] {summary}")

    return summary


def run_watch_monitor() -> str:
    """关注中监控（每1小时）：检查 watching 品种的升降级
    升级: DL=S, PT>=A, LK>=A, SF=1st, TY>=A → focused
    降级: 不满足 DL=S, PT>=B, LK>=B, SF<=2nd → idle
    """
    stocks = get_stocks_by_watch_status('watching')
    upgraded = 0
    downgraded = 0

    for stock in stocks:
        grades = get_effective_grades(stock)

        if meets_focused_criteria(grades):
            transition_stock(stock['id'], 'focused', '关注中监控-满足重点条件')
            upgraded += 1
            logger.info(f"[关注中监控] {stock['symbol']} 升级到focused grades={grades}")
            _notify_upgrade(stock, 'watching', 'focused')
            continue

        if not meets_watching_criteria(grades):
            transition_stock(stock['id'], 'idle', '关注中监控-不满足关注条件')
            downgraded += 1
            logger.info(f"[关注中监控] {stock['symbol']} 降级到idle grades={grades}")

    summary = (f"扫描 {len(stocks)} 只watching品种，"
               f"{upgraded} 只升级，{downgraded} 只降级")
    logger.info(f"[关注中监控] {summary}")
    return summary


def run_focus_monitor() -> str:
    """重点列表监控（每5分钟）：检查 focused 品种的下单条件和降级
    下单: DL=S, PT>=A, LK>=A, SF=1st, TY>=A, DN>=A → holding
    降级: 不满足重点条件 → watching 或 idle
    """
    stocks = get_stocks_by_watch_status('focused')
    triggered = 0
    downgraded = 0

    for stock in stocks:
        grades = get_effective_grades(stock)

        # 检查是否满足下单条件
        if meets_order_criteria(grades):
            try:
                from web.services.trader import execute_paper_trade
                result = execute_paper_trade(stock)
                if result and result.get('ok'):
                    transition_stock(stock['id'], 'holding', 'DN触发-模拟下单')
                    triggered += 1
                    logger.info(f"[重点监控] {stock['symbol']} DN触发下单 grades={grades} result={result}")
                    _notify_trade(stock, result)
            except Exception as e:
                logger.error(f"[重点监控] 模拟下单失败 {stock['symbol']}: {e}\n{traceback.format_exc()}")
            continue

        # 检查是否不满足 focused 条件 (DL=S, PT>=A, LK>=A)
        if not meets_focused_criteria(grades):
            if meets_watching_criteria(grades):
                # 仍满足关注条件，降到 watching
                transition_stock(stock['id'], 'watching', '重点监控-不满足重点条件')
                downgraded += 1
                logger.info(f"[重点监控] {stock['symbol']} 降级到watching grades={grades}")
                _notify_downgrade(stock, 'focused', 'watching')
            else:
                # 连关注条件都不满足，直接降到 idle
                transition_stock(stock['id'], 'idle', '重点监控-不满足关注条件')
                downgraded += 1
                logger.info(f"[重点监控] {stock['symbol']} 跳降到idle grades={grades}")
                _notify_downgrade(stock, 'focused', 'idle')

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
            logger.error(f"[持仓检查] 检查持仓失败 {stock['symbol']}: {e}\n{traceback.format_exc()}")

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
                logger.error(f"[价格刷新] 获取 {stock['symbol']} 最新价失败: {e}\n{traceback.format_exc()}")

    summary = f"刷新 {total} 只品种价格，成功 {updated}"
    logger.info(f"[价格刷新] {summary}")
    return summary


def _fetch_latest_price(symbol: str, market: str) -> Optional[float]:
    """获取最新价格 — 多数据源容错"""
    if market == 'cn':
        # A股：Sina（稳定）→ yfinance → Yahoo v8
        price = _fetch_price_sina(symbol)
        if price:
            return price
        price = _fetch_price_yfinance(symbol, market)
        if price:
            return price
        return _fetch_price_yahoo_v8(symbol, market)
    else:
        # 港股/美股：Sina（快速稳定）→ yfinance → Yahoo v8
        price = _fetch_price_sina_foreign(symbol, market)
        if price:
            return price
        price = _fetch_price_yfinance(symbol, market)
        if price:
            return price
        return _fetch_price_yahoo_v8(symbol, market)


def _fetch_price_sina(symbol: str) -> Optional[float]:
    """通过 Sina Finance 获取 A 股最新价"""
    try:
        import requests
        prefix = 'sh' if symbol.startswith('6') else 'sz'
        url = f"https://hq.sinajs.cn/list={prefix}{symbol}"
        headers = {
            'Referer': 'https://finance.sina.com.cn',
            'User-Agent': 'Mozilla/5.0',
        }
        resp = requests.get(url, headers=headers, timeout=10)
        resp.encoding = 'gbk'
        text = resp.text
        if '="' in text:
            fields = text.split('="')[1].split(',')
            # 字段3是当前价，如果为0或空则取昨收(字段2)
            if len(fields) > 3:
                cur = float(fields[3]) if fields[3] else 0
                if cur > 0:
                    return cur
                prev = float(fields[2]) if fields[2] else 0
                if prev > 0:
                    return prev
    except Exception as e:
        logger.debug(f"Sina获取 {symbol} 价格失败: {e}")
    return None


def _fetch_price_yfinance(symbol: str, market: str) -> Optional[float]:
    """通过 yfinance 获取最新价"""
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
        logger.debug(f"yfinance获取 {symbol} 价格失败: {e}")
        return None


def _fetch_price_yahoo_v8(symbol: str, market: str) -> Optional[float]:
    """通过 Yahoo Finance v8 API 获取最新价（yfinance 备用）"""
    try:
        import requests
        if market == 'hk':
            yahoo_sym = f"{int(symbol):04d}.HK"
        elif market == 'cn':
            suffix = '.SS' if symbol.startswith('6') else '.SZ'
            yahoo_sym = f"{symbol}{suffix}"
        else:
            yahoo_sym = symbol

        url = f'https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_sym}'
        params = {'range': '1d', 'interval': '1d'}
        headers = {'User-Agent': 'Mozilla/5.0'}
        resp = requests.get(url, params=params, headers=headers, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            meta = data.get('chart', {}).get('result', [{}])[0].get('meta', {})
            price = meta.get('regularMarketPrice') or meta.get('previousClose')
            if price:
                return float(price)
    except Exception as e:
        logger.debug(f"Yahoo v8获取 {symbol} 价格失败: {e}")
    return None


def _fetch_price_sina_foreign(symbol: str, market: str) -> Optional[float]:
    """通过 Sina Finance 获取港股/美股最新价（Yahoo 全部失败时的最终备用）"""
    try:
        import requests
        if market == 'hk':
            # 港股：rt_hkXXXXX 格式，当前价在字段6
            sina_sym = f"rt_hk{symbol.zfill(5)}"
            url = f"https://hq.sinajs.cn/list={sina_sym}"
            headers = {'Referer': 'https://finance.sina.com.cn', 'User-Agent': 'Mozilla/5.0'}
            resp = requests.get(url, headers=headers, timeout=10)
            resp.encoding = 'gbk'
            text = resp.text
            if '="' in text:
                fields = text.split('="')[1].split(',')
                if len(fields) > 6:
                    cur = float(fields[6]) if fields[6] else 0
                    if cur > 0:
                        return cur
                    prev = float(fields[2]) if fields[2] else 0
                    if prev > 0:
                        return prev
        elif market == 'us':
            # 美股：gb_XXXX 格式（小写），当前价在字段1
            sina_sym = f"gb_{symbol.lower()}"
            url = f"https://hq.sinajs.cn/list={sina_sym}"
            headers = {'Referer': 'https://finance.sina.com.cn', 'User-Agent': 'Mozilla/5.0'}
            resp = requests.get(url, headers=headers, timeout=10)
            resp.encoding = 'gbk'
            text = resp.text
            if '="' in text:
                fields = text.split('="')[1].split(',')
                if len(fields) > 1:
                    cur = float(fields[1]) if fields[1] else 0
                    if cur > 0:
                        return cur
    except Exception as e:
        logger.debug(f"Sina获取 {symbol}({market}) 价格失败: {e}")
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
