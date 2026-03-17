"""定时任务函数定义"""
import logging
import time
import traceback
from datetime import datetime
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

_SHANGHAI_TZ = ZoneInfo('Asia/Shanghai')
_EASTERN_TZ = ZoneInfo('US/Eastern')


def _is_market_hours() -> bool:
    """判断当前是否在交易时段（仅盘中，不含盘前盘后）

    覆盖三个市场：
    - A股 + 港股: 周一~周五 09:00 - 16:10 北京时间
    - 美股: 周一~周五 09:30 - 16:00 美东时间（自动适配夏令时/冬令时）
    """
    # 亚洲时段: 用北京时间判断
    now_sh = datetime.now(_SHANGHAI_TZ)
    wd_sh = now_sh.weekday()
    t_sh = now_sh.hour * 60 + now_sh.minute

    if 0 <= wd_sh <= 4 and 9 * 60 <= t_sh <= 16 * 60 + 10:
        return True

    # 美股时段: 用美东时间判断，自动处理 EST/EDT 切换
    now_et = datetime.now(_EASTERN_TZ)
    wd_et = now_et.weekday()
    t_et = now_et.hour * 60 + now_et.minute

    if 0 <= wd_et <= 4 and 9 * 60 + 30 <= t_et <= 16 * 60:
        return True

    return False


def daily_scan():
    """每周品种扫描：检查idle池，满足条件的升级到watching"""
    logger.info("[任务] 品种扫描 - 执行中...")
    start = time.time()
    try:
        from web.services.monitor import run_daily_scan, refresh_stock_data
        from web.services.state_machine import get_stocks_by_watch_status
        stocks = get_stocks_by_watch_status('idle')
        refreshed = refresh_stock_data(stocks)
        logger.info(f"[任务] 数据刷新 {refreshed}/{len(stocks)} 只")
        result = run_daily_scan()
        elapsed = int((time.time() - start) * 1000)
        summary = f"数据刷新 {refreshed} 只; {result}"
        logger.info(f"[任务] 品种扫描完成 ({elapsed}ms)")
        return summary
    except Exception as e:
        logger.error(f"[任务] 品种扫描失败: {e}\n{traceback.format_exc()}")
        raise


def focus_monitor():
    """重点列表监控（每5分钟）：检查DN触发和降级，仅在交易时段运行"""
    if not _is_market_hours():
        logger.debug("[任务] 重点列表监控 - 非交易时段，跳过")
        return "非交易时段，跳过"
    logger.info("[任务] 重点列表监控 - 执行中...")
    start = time.time()
    try:
        from web.services.monitor import run_focus_monitor, refresh_stock_data
        from web.services.state_machine import get_stocks_by_watch_status
        stocks = get_stocks_by_watch_status('focused')
        refreshed = refresh_stock_data(stocks)
        logger.info(f"[任务] 数据刷新 {refreshed}/{len(stocks)} 只")
        result = run_focus_monitor()
        elapsed = int((time.time() - start) * 1000)
        summary = f"数据刷新 {refreshed} 只; {result}"
        logger.info(f"[任务] 重点列表监控完成 ({elapsed}ms)")
        return summary
    except Exception as e:
        logger.error(f"[任务] 重点列表监控失败: {e}\n{traceback.format_exc()}")
        raise


def watch_monitor():
    """关注中监控（每1小时）：检查升级/降级，仅在交易时段运行"""
    if not _is_market_hours():
        logger.debug("[任务] 关注中监控 - 非交易时段，跳过")
        return "非交易时段，跳过"
    logger.info("[任务] 关注中监控 - 执行中...")
    start = time.time()
    try:
        from web.services.monitor import run_watch_monitor, refresh_stock_data
        from web.services.state_machine import get_stocks_by_watch_status
        stocks = get_stocks_by_watch_status('watching')
        refreshed = refresh_stock_data(stocks)
        logger.info(f"[任务] 数据刷新 {refreshed}/{len(stocks)} 只")
        result = run_watch_monitor()
        elapsed = int((time.time() - start) * 1000)
        summary = f"数据刷新 {refreshed} 只; {result}"
        logger.info(f"[任务] 关注中监控完成 ({elapsed}ms)")
        return summary
    except Exception as e:
        logger.error(f"[任务] 关注中监控失败: {e}\n{traceback.format_exc()}")
        raise


def news_collect():
    """新闻采集（每30分钟）"""
    logger.info("[任务] 新闻采集 - 执行中...")
    start = time.time()
    try:
        from web.services.news import collect_news_for_stocks
        result = collect_news_for_stocks()
        elapsed = int((time.time() - start) * 1000)
        logger.info(f"[任务] 新闻采集完成 ({elapsed}ms)")
        return result
    except Exception as e:
        logger.error(f"[任务] 新闻采集失败: {e}\n{traceback.format_exc()}")
        raise


def daily_report():
    """日报推送：收盘后汇总监控概览和持仓盈亏"""
    logger.info("[任务] 日报推送 - 执行中...")
    try:
        from web.services.notifier import get_notifier
        from web.services.state_machine import get_stocks_by_watch_status
        from web.services.trader import get_account_summary

        notifier = get_notifier()
        if not notifier:
            return "未配置通知渠道，跳过日报"

        # 收集数据
        focused = get_stocks_by_watch_status('focused')
        watching = get_stocks_by_watch_status('watching')
        holding = get_stocks_by_watch_status('holding')
        account = get_account_summary()

        today = datetime.now().strftime('%Y-%m-%d')

        fields = {
            "日期": today,
            "重点关注": f"{len(focused)} 只",
            "关注中": f"{len(watching)} 只",
            "当前持仓": f"{len(holding)} 只",
        }

        if account['account']['total_trades'] > 0:
            fields["累计交易"] = str(account['account']['total_trades'])
            fields["胜率"] = f"{account['win_rate']}%"
            fields["总盈亏"] = f"${account['account']['total_pnl']:.2f}"
            fields["最大回撤"] = f"{account['account']['max_drawdown']}%"

        if account['positions']:
            pos_lines = []
            for p in account['positions']:
                emoji = "+" if p.get('unrealized_pnl', 0) >= 0 else ""
                pos_lines.append(
                    f"{p['symbol']}: {emoji}{p.get('unrealized_pnl', 0):.0f} "
                    f"({emoji}{p.get('unrealized_pnl_pct', 0):.1f}%)"
                )
            fields["持仓详情"] = "\n".join(pos_lines)

        notifier.send_card(f"ZQ-Trade 日报 {today}", fields)
        return f"日报已推送: {today}"

    except Exception as e:
        logger.error(f"[任务] 日报推送失败: {e}\n{traceback.format_exc()}")
        raise
