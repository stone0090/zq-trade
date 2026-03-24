"""
数据获取模块

支持 A股/港股/美股 小时级 K 线数据的智能增量获取与本地缓存。
通过股票代码自动识别市场，按市场分目录缓存。
"""
import os
import time
import logging
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

# ─── 代理与国内域名旁路配置（模块加载时一次性设置）───
#
# 问题：Clash 设置了 Windows 系统代理 → Python requests 自动读取 →
#   1) 国内源（东财/新浪）走代理反而连接失败
#   2) yfinance 的 curl-cffi 没读到显式代理环境变量 → 直连 Yahoo 被 GFW 拦截
#
# 方案：
#   - 自动检测本地代理 → 设置 HTTPS_PROXY/HTTP_PROXY（让所有库都走代理）
#   - 设置 NO_PROXY 让国内域名绕过代理直连

_DOMESTIC_NO_PROXY = (
    'eastmoney.com,push2his.eastmoney.com,push2.eastmoney.com,'
    'sinajs.cn,sina.com.cn,quotes.sina.cn,'
    'baostock.com'
)
_existing_no_proxy = os.environ.get('NO_PROXY', '')
if _existing_no_proxy:
    os.environ['NO_PROXY'] = f"{_existing_no_proxy},{_DOMESTIC_NO_PROXY}"
else:
    os.environ['NO_PROXY'] = _DOMESTIC_NO_PROXY

# 自动检测本地代理并设置环境变量（让 yfinance/curl-cffi 等所有库都能走代理）
if not (os.environ.get('HTTPS_PROXY') or os.environ.get('HTTP_PROXY')
        or os.environ.get('https_proxy') or os.environ.get('http_proxy')):
    import socket as _socket
    for _port in (7890, 10809, 1080):
        try:
            _s = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
            _s.settimeout(0.3)
            _result = _s.connect_ex(('127.0.0.1', _port))
            _s.close()
            if _result == 0:
                _proxy_url = f'http://127.0.0.1:{_port}'
                os.environ['HTTPS_PROXY'] = _proxy_url
                os.environ['HTTP_PROXY'] = _proxy_url
                logger.info(f"自动检测到本地代理 {_proxy_url}, 已设置 HTTPS_PROXY/HTTP_PROXY "
                            f"(国内域名通过 NO_PROXY 绕过)")
                break
        except Exception:
            continue


# ─── API 请求节流 ───
# 记录各数据源上次请求时间，控制请求间隔避免被限流
_last_request_time = {}
_MIN_INTERVAL = {
    'akshare': 1.0,    # akshare(东财) 最少间隔1秒
    'sina': 1.0,       # Sina Finance 最少间隔1秒
    'yahoo': 1.5,      # Yahoo Finance 最少间隔1.5秒
    'yfinance': 1.0,   # yfinance库 最少间隔1秒
    'efinance': 1.0,   # efinance(东财另一端点) 最少间隔1秒
    'baostock': 0.5,   # baostock 最少间隔0.5秒
    'twelvedata': 8.0, # Twelve Data 免费层 8次/分钟
    'akshare_em': 2.0, # 东财港美股分钟线 最少间隔2秒
    'alphavantage': 12.0,  # Alpha Vantage 免费层 5次/分钟
}


def _throttle(source: str):
    """API 请求节流：确保同一数据源的请求间隔不低于最小间隔"""
    min_interval = _MIN_INTERVAL.get(source, 1.0)
    now = time.monotonic()
    last = _last_request_time.get(source, 0)
    elapsed = now - last
    if elapsed < min_interval:
        time.sleep(min_interval - elapsed)
    _last_request_time[source] = time.monotonic()


# ─── 数据源健康度追踪 ───
_SOURCE_HEALTH = {}  # {source: {'fails': int, 'cooldown_until': float}}
_COOLDOWN_SCHEDULE = [0, 30, 120, 600, 1800]  # 冷却秒数，按连续失败次数递增


def _mark_success(source: str):
    """标记数据源请求成功，重置失败计数"""
    h = _SOURCE_HEALTH.setdefault(source, {'fails': 0, 'cooldown_until': 0.0})
    h['fails'] = 0
    h['cooldown_until'] = 0.0


def _mark_failure(source: str):
    """标记数据源请求失败，设置指数退避冷却期"""
    h = _SOURCE_HEALTH.setdefault(source, {'fails': 0, 'cooldown_until': 0.0})
    h['fails'] += 1
    idx = min(h['fails'], len(_COOLDOWN_SCHEDULE) - 1)
    h['cooldown_until'] = time.monotonic() + _COOLDOWN_SCHEDULE[idx]


def _is_available(source: str) -> bool:
    """检查数据源是否可用（不在冷却期）"""
    h = _SOURCE_HEALTH.get(source)
    if not h:
        return True
    return time.monotonic() >= h['cooldown_until']


# ─── 可选依赖库检测 ───
_LIB_AVAILABLE = {}  # 仅缓存 True，False 不缓存（允许运行中安装后生效）


def _check_lib(name: str) -> bool:
    """检查可选依赖库是否已安装（仅缓存成功结果）"""
    if _LIB_AVAILABLE.get(name):
        return True
    try:
        if name == 'efinance':
            import efinance  # noqa: F401
        elif name == 'baostock':
            import baostock  # noqa: F401
        elif name == 'twelvedata':
            return bool(_get_twelvedata_key())
        else:
            return True
        _LIB_AVAILABLE[name] = True
        return True
    except Exception:
        return False


def _source_priority(name: str) -> int:
    """计算数据源优先级分数（越小越优先）：无失败=0，冷却中=失败次数"""
    h = _SOURCE_HEALTH.get(name)
    if not h:
        return 0
    if time.monotonic() >= h['cooldown_until']:
        return 0  # 冷却结束，恢复正常优先级
    return h['fails']


def _try_sources(sources: list):
    """
    按优先级逐个尝试数据源列表（动态排序）。
    sources: [(name, fetch_fn), ...]
    失败次数多的源排到后面，冷却中的源被跳过（除非是最后一个）。
    返回: (source_name, DataFrame)
    """
    # 按健康度动态排序：失败次数少的优先，同等失败次数保持原始顺序
    sorted_sources = sorted(enumerate(sources), key=lambda x: (_source_priority(x[1][0]), x[0]))
    sorted_sources = [s for _, s in sorted_sources]

    errors = []
    for i, (name, fetch_fn) in enumerate(sorted_sources):
        is_last = (i == len(sorted_sources) - 1)
        if not _is_available(name) and not is_last:
            h = _SOURCE_HEALTH.get(name, {})
            remaining = h.get('cooldown_until', 0) - time.monotonic()
            logger.info(f"  跳过 {name} (冷却中, 剩余 {remaining:.0f}s, 连续失败{h.get('fails', 0)}次)")
            continue
        try:
            df = fetch_fn()
            _mark_success(name)
            return name, df
        except Exception as e:
            _mark_failure(name)
            errors.append(f"{name}: {e}")
            logger.warning(f"  {name} 获取失败: {e}")
    raise Exception(f"所有数据源均失败: {'; '.join(errors)}")


# ─── 每日K线根数（用于从目标根数反推天数）───

_BARS_PER_DAY = {
    'cn': 4,    # A股：10:30, 11:30, 14:00, 15:00
    'hk': 6,    # 港股：10:00~16:00 (午休12:00~13:00)
    'us': 7,    # 美股：09:30~16:00
}


# ─── 市场识别 ───

def detect_market(symbol: str) -> str:
    """根据代码格式识别市场: 6位数字→'cn', ≤5位数字→'hk', 含字母→'us'"""
    if symbol.isdigit():
        return 'cn' if len(symbol) >= 6 else 'hk'
    return 'us'


# ─── 缓存路径 ───

def _get_data_dir() -> Path:
    """获取数据缓存根目录"""
    data_dir = Path(__file__).resolve().parent.parent.parent / 'data'
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def _cache_path(symbol: str) -> Path:
    """获取某只股票的缓存文件路径（按市场分目录）"""
    market = detect_market(symbol)
    market_dir = _get_data_dir() / market
    market_dir.mkdir(parents=True, exist_ok=True)
    return market_dir / f"{symbol}_hourly.csv"


# ─── 智能增量获取（主入口）───

def fetch_kline_smart(symbol: str,
                      end_date: str = None,
                      bars: int = 300) -> pd.DataFrame:
    """
    智能增量获取小时K线数据。

    1. 根据截止日期和目标根数，计算需要的时间范围
    2. 读取本地缓存，判断是否需要增量拉取
    3. 增量拉取后与本地合并、去重、保存
    4. 返回截止日期前最近 bars 根数据

    Args:
        symbol: 股票代码（纯数字=A股，含字母=美股）
        end_date: 截止日期 'YYYY-MM-DD'，默认今天
        bars: 目标K线根数，默认300

    Returns:
        DataFrame(Open, High, Low, Close, Volume)，索引为 DatetimeIndex
    """
    market = detect_market(symbol)

    # 计算截止时间
    if end_date is None:
        end_dt = datetime.now()
    else:
        end_dt = datetime.strptime(end_date, '%Y-%m-%d')
    # 设置到当天收盘时间
    if market == 'cn':
        end_dt = end_dt.replace(hour=15, minute=0, second=0, microsecond=0)
    elif market == 'hk':
        end_dt = end_dt.replace(hour=16, minute=0, second=0, microsecond=0)
    else:
        end_dt = end_dt.replace(hour=16, minute=0, second=0, microsecond=0)

    # 从目标根数反推需要的自然天数（加余量）
    bars_per_day = _BARS_PER_DAY.get(market, 4)
    trading_days_needed = (bars // bars_per_day) + 10  # 余量
    calendar_days = int(trading_days_needed * 1.5)     # 交易日→自然日
    start_dt = end_dt - timedelta(days=calendar_days)

    # 根据所需自然天数选择合适的 Yahoo period（避免过度拉取）
    if calendar_days <= 5:
        _needed_period = '5d'
    elif calendar_days <= 25:
        _needed_period = '1mo'
    elif calendar_days <= 80:
        _needed_period = '3mo'
    else:
        _needed_period = '6mo'

    cache_file = _cache_path(symbol)

    # ─── 港股/美股：支持增量获取 ───
    if market in ('hk', 'us'):
        local_df = None
        if cache_file.exists():
            try:
                local_df = load_from_csv(str(cache_file))
                if local_df.empty:
                    local_df = None
            except Exception:
                local_df = None

        # 检测本地缓存是否为日线数据（相邻时间差中位数 > 20小时 → 日线）
        _is_daily_cache = False
        if local_df is not None and len(local_df) > 5:
            time_diffs = local_df.index.to_series().diff().dropna()
            median_hours = time_diffs.median().total_seconds() / 3600
            if median_hours > 20:
                _is_daily_cache = True
                logger.warning(f"[{market.upper()}] {symbol} 本地缓存为日线数据(间隔中位数 {median_hours:.0f}h), 将尝试重新拉取小时线")

        if local_df is not None and not _is_daily_cache:
            local_end = local_df.index[-1]
            local_start = local_df.index[0]
            logger.info(f"[{market.upper()}] {symbol} 本地缓存 {len(local_df)} 根, "
                        f"范围 {local_start.strftime('%Y-%m-%d %H:%M')} ~ {local_end.strftime('%Y-%m-%d %H:%M')}, "
                        f"请求截止 {end_dt.strftime('%Y-%m-%d %H:%M')}")

            # 本地数据已覆盖截止日期
            if local_end >= end_dt:
                available = local_df[local_df.index <= end_dt]
                if len(available) >= bars:
                    result = available.tail(bars)
                    logger.info(f"[{market.upper()}] {symbol} 缓存足够, 直接截取 {len(result)} 根 (渠道: 本地缓存)")
                    return result
                logger.info(f"[{market.upper()}] {symbol} 缓存时间足够但仅 {len(available)} 根 < {bars}, 需重新拉取")
                # 数量不够，走下面的无缓存全量拉取路径
                local_df = None

            # 有缓存但不够新 → 增量拉取
            gap_days = (end_dt - local_end).days

            # 优化：如果缓存最新日期已覆盖请求截止日期当天，直接返回缓存
            # 避免因为时间戳精度问题（如 15:00 vs 16:00）或跨天请求而触发不必要的网络请求
            if local_end.date() >= end_dt.date():
                merged = local_df
                logger.info(f"[{market.upper()}] {symbol} 缓存最新日期 {local_end.strftime('%Y-%m-%d')} 已覆盖请求日期 {end_dt.strftime('%Y-%m-%d')}, 跳过拉取")
            # 优化：检查 gap 中是否存在交易日（周一~周五）
            # 纯周末/节假日无新数据，跳过网络请求
            else:
                _gap_has_trading_day = False
                for _d in range(1, gap_days + 1):
                    if (local_end + timedelta(days=_d)).weekday() < 5:
                        _gap_has_trading_day = True
                        break

                if not _gap_has_trading_day and gap_days > 0:
                    # gap 全是非交易日，无需网络请求
                    merged = local_df
                    logger.info(f"[{market.upper()}] {symbol} 差 {gap_days} 天均为非交易日, 跳过拉取")
                else:
                    # 需要增量拉取
                    if gap_days <= 5:
                        period = '5d'
                    elif gap_days <= 30:
                        period = '1mo'
                    else:
                        period = '6mo'

                    yahoo_sym = _to_yahoo_symbol(symbol, market)
                    logger.info(f"[{market.upper()}] {symbol} 缓存差 {gap_days} 天, 增量拉取中...")
                    new_df = _fetch_hk_us(yahoo_sym, period=period, symbol=symbol, market=market)
                    if new_df is not None and not new_df.empty:
                        # 只保留缓存截止时间之后的新数据
                        new_df = new_df[new_df.index > local_end]
                        if not new_df.empty:
                            merged = pd.concat([local_df, new_df])
                            merged = merged[~merged.index.duplicated(keep='last')].sort_index()
                            logger.info(f"[{market.upper()}] {symbol} 增量 +{len(new_df)} 根, 合并后共 {len(merged)} 根")
                        else:
                            merged = local_df
                            logger.info(f"[{market.upper()}] {symbol} 无新增数据, 使用本地 {len(merged)} 根")
                    else:
                        merged = local_df
                        logger.warning(f"[{market.upper()}] {symbol} 增量获取失败, 使用本地 {len(merged)} 根")

                    save_to_csv(merged, str(cache_file))
            result = merged[merged.index <= end_dt].tail(bars)
            logger.info(f"[{market.upper()}] {symbol} 数据就绪: {len(result)} 根K线, "
                        f"范围 {result.index[0].strftime('%Y-%m-%d')} ~ {result.index[-1].strftime('%Y-%m-%d')}")
            return result

        # 无缓存 或 日线缓存需要重拉 → 按需拉取小时线（不多拉）
        if _is_daily_cache:
            logger.info(f"[{market.upper()}] {symbol} 清除日线缓存, 重新拉取小时线 (period={_needed_period})...")
        else:
            logger.info(f"[{market.upper()}] {symbol} 无本地缓存, 拉取小时线 (period={_needed_period})...")
        yahoo_sym = _to_yahoo_symbol(symbol, market)
        merged = _fetch_hk_us(yahoo_sym, period=_needed_period, symbol=symbol, market=market)
        if merged is None or merged.empty:
            raise ValueError(f"未能获取到 {symbol} 的有效数据 (所有数据源均失败)")

        save_to_csv(merged, str(cache_file))
        result = merged[merged.index <= end_dt].tail(bars)
        logger.info(f"[{market.upper()}] {symbol} 数据就绪: {len(result)} 根K线, "
                    f"范围 {result.index[0].strftime('%Y-%m-%d')} ~ {result.index[-1].strftime('%Y-%m-%d')}")
        return result

    # ─── A股：原有逻辑 ───

    # ─── 读取本地缓存 ───
    local_df = None
    if cache_file.exists():
        try:
            local_df = load_from_csv(str(cache_file))
            if local_df.empty:
                local_df = None
        except Exception:
            local_df = None

    # ─── 判断是否需要拉取 ───
    if local_df is not None:
        local_end = local_df.index[-1]
        local_start = local_df.index[0]
        logger.info(f"[CN] {symbol} 本地缓存 {len(local_df)} 根, "
                    f"范围 {local_start.strftime('%Y-%m-%d %H:%M')} ~ {local_end.strftime('%Y-%m-%d %H:%M')}")

        # 本地数据已覆盖截止日期
        if local_end >= end_dt:
            available = local_df[local_df.index <= end_dt]
            if len(available) >= bars:
                result = available.tail(bars)
                logger.info(f"[CN] {symbol} 缓存足够, 直接截取 {len(result)} 根 (渠道: 本地缓存)")
                return result
            # 时间覆盖但数量不够 → 跳过增量拉取，直接向前补数据
            logger.info(f"[CN] {symbol} 缓存时间足够但仅 {len(available)} 根 < {bars}, 需向前补充")
            merged = local_df
        else:
            # 本地数据不够新 → 增量拉取
            gap_days = (end_dt - local_end).days
            incr_start = local_end + timedelta(hours=1)
            incr_start_str = incr_start.strftime('%Y-%m-%d %H:%M:%S')
            end_str = end_dt.strftime('%Y-%m-%d %H:%M:%S')

            logger.info(f"[CN] {symbol} 缓存差 {gap_days} 天, 增量拉取中...")
            try:
                new_df = _fetch_cn(symbol, incr_start_str, end_str)
                if new_df is not None and not new_df.empty:
                    merged = pd.concat([local_df, new_df])
                    merged = merged[~merged.index.duplicated(keep='last')].sort_index()
                    logger.info(f"[CN] {symbol} 增量 +{len(new_df)} 根, 合并后共 {len(merged)} 根")
                else:
                    merged = local_df
                    logger.info(f"[CN] {symbol} 无新增数据, 使用本地 {len(merged)} 根")
            except Exception as e:
                logger.warning(f"[CN] {symbol} 增量拉取失败: {e}, 使用本地数据")
                merged = local_df

        # 同时检查是否需要往前补数据
        if local_start > start_dt:
            prepend_end_str = (local_start - timedelta(hours=1)).strftime('%Y-%m-%d %H:%M:%S')
            prepend_start_str = start_dt.strftime('%Y-%m-%d %H:%M:%S')
            logger.info(f"[CN] {symbol} 向前补充: {prepend_start_str[:10]} ~ {prepend_end_str[:10]}")
            try:
                old_df = _fetch_cn(symbol, prepend_start_str, prepend_end_str)
                if old_df is not None and not old_df.empty:
                    merged = pd.concat([old_df, merged])
                    merged = merged[~merged.index.duplicated(keep='last')].sort_index()
                    logger.info(f"[CN] {symbol} 向前补充 +{len(old_df)} 根, 合并后共 {len(merged)} 根")
            except Exception as e:
                logger.warning(f"[CN] {symbol} 向前补充失败: {e}")
    else:
        # 无本地缓存 → 全量拉取
        start_str = start_dt.strftime('%Y-%m-%d %H:%M:%S')
        end_str = end_dt.strftime('%Y-%m-%d %H:%M:%S')
        logger.info(f"[CN] {symbol} 无本地缓存, 全量拉取...")
        merged = _fetch_cn(symbol, start_str, end_str)

    if merged is None or merged.empty:
        raise ValueError(f"未能获取到 {symbol} 的有效数据")

    # 保存合并后的完整数据
    save_to_csv(merged, str(cache_file))

    # 截取截止日期前最近 bars 根
    result = merged[merged.index <= end_dt].tail(bars)
    logger.info(f"[CN] {symbol} 数据就绪: {len(result)} 根小时K线, "
                f"范围 {result.index[0].strftime('%Y-%m-%d %H:%M')} ~ {result.index[-1].strftime('%Y-%m-%d %H:%M')}")

    return result


# ─── 东财可达性检测 ───

_em_reachable_cache = {'result': None, 'ts': 0}

def _is_eastmoney_reachable() -> bool:
    """检测东财API是否可达（HTTPS HEAD探测，120秒缓存结果避免重复探测）"""
    now = time.monotonic()
    cache = _em_reachable_cache
    if cache['result'] is not None and (now - cache['ts']) < 120:
        return cache['result']
    try:
        import requests as _rq
        _rq.head('https://push2his.eastmoney.com/', timeout=3)
        cache['result'] = True
        cache['ts'] = now
        return True
    except Exception:
        logger.info("  东财API不可达，跳过相关数据源（120秒内不重试）")
        cache['result'] = False
        cache['ts'] = now
        return False


# ─── A股数据拉取 ───

def _fetch_cn(symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
    """拉取A股小时K线 — 多源轮换"""
    # 预计算 Sina 需要的 datalen
    start_dt = pd.to_datetime(start_date)
    end_dt = pd.to_datetime(end_date)
    days_span = (end_dt - start_dt).days
    sina_datalen = max(1500, days_span * 4)

    def _sina_with_filter():
        df = _fetch_via_sina(symbol, datalen=sina_datalen)
        if df is not None and not df.empty:
            df2 = df[(df.index >= start_dt) & (df.index <= end_dt)]
            if not df2.empty:
                return df2
        raise ValueError("Sina Finance 未返回有效数据")

    sources = []
    em_ok = _is_eastmoney_reachable()
    if em_ok:
        sources.append(('akshare', lambda: _fetch_via_akshare(symbol, start_date, end_date)))
        if _check_lib('efinance'):
            sources.append(('efinance', lambda: _fetch_via_efinance(symbol, start_date, end_date)))
    sources.append(('sina', _sina_with_filter))
    if _check_lib('baostock'):
        sources.append(('baostock', lambda: _fetch_via_baostock(symbol, start_date, end_date)))

    source_name, df = _try_sources(sources)
    logger.info(f"  [A股] {symbol} 通过 {source_name} 获取 {len(df)} 根")
    return df


def _fetch_via_akshare(symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
    """通过 akshare (eastmoney) 获取数据"""
    try:
        import akshare as ak
    except ImportError:
        raise ImportError("请先安装 akshare: pip install akshare")

    _throttle('akshare')
    df = ak.stock_zh_a_hist_min_em(
        symbol=symbol,
        start_date=start_date,
        end_date=end_date,
        period='60',
        adjust='qfq'
    )

    if df is None or df.empty:
        raise ValueError(f"akshare 未返回 {symbol} 的数据")

    df = _standardize_columns(df)
    return df


def _fetch_via_sina(symbol: str, datalen: int = 1500) -> pd.DataFrame:
    """通过 Sina Finance API 获取小时级K线数据（备用源）"""
    import requests
    import json

    _throttle('sina')
    market = 'sh' if symbol.startswith('6') else 'sz'
    sina_symbol = f"{market}{symbol}"

    url = (
        f"https://quotes.sina.cn/cn/api/jsonp_v2.php/var/"
        f"CN_MarketDataService.getKLineData"
        f"?symbol={sina_symbol}&scale=60&ma=no&datalen={datalen}"
    )

    resp = requests.get(url, headers={'Referer': 'https://finance.sina.com.cn'}, timeout=15)
    resp.raise_for_status()

    text = resp.text
    start_idx = text.index('[')
    end_idx = text.rindex(']') + 1
    data = json.loads(text[start_idx:end_idx])

    if not data:
        raise ValueError("Sina Finance 返回空数据")

    df = pd.DataFrame(data)
    df.rename(columns={
        'day': 'datetime',
        'open': 'Open',
        'high': 'High',
        'low': 'Low',
        'close': 'Close',
        'volume': 'Volume'
    }, inplace=True)
    df['datetime'] = pd.to_datetime(df['datetime'])
    df.set_index('datetime', inplace=True)
    df = df[['Open', 'High', 'Low', 'Close', 'Volume']]
    for col in df.columns:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    df.sort_index(inplace=True)

    return df


def _fetch_via_efinance(symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
    """通过 efinance (东财另一端点) 获取A股小时K线"""
    import efinance as ef

    _throttle('efinance')
    beg = start_date[:10].replace('-', '')  # YYYYMMDD
    end = end_date[:10].replace('-', '')
    df = ef.stock.get_quote_history(symbol, beg=beg, end=end, klt=60, fqt=1)

    if df is None or df.empty:
        raise ValueError(f"efinance 未返回 {symbol} 的数据")

    df = _standardize_columns(df)
    logger.info(f"  efinance 获取 {symbol} {len(df)} 根")
    return df


def _fetch_via_baostock(symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
    """通过 baostock 获取A股60分钟K线（数据延迟约1天）"""
    import baostock as bs

    _throttle('baostock')
    prefix = 'sh' if symbol.startswith('6') else 'sz'
    code = f"{prefix}.{symbol}"

    bs.login()
    try:
        rs = bs.query_history_k_data_plus(
            code,
            "date,time,open,high,low,close,volume",
            start_date=start_date[:10],
            end_date=end_date[:10],
            frequency="60",
            adjustflag="2"  # 前复权
        )
        rows = []
        while (rs.error_code == '0') and rs.next():
            rows.append(rs.get_row_data())
    finally:
        bs.logout()

    if not rows:
        raise ValueError(f"baostock 未返回 {symbol} 的数据")

    df = pd.DataFrame(rows, columns=rs.fields)
    # baostock time 格式: "YYYYMMDDHHmmssSSS" (17位)
    df['datetime'] = pd.to_datetime(df['time'].str[:12], format='%Y%m%d%H%M')
    df.set_index('datetime', inplace=True)
    df.rename(columns={
        'open': 'Open', 'high': 'High', 'low': 'Low',
        'close': 'Close', 'volume': 'Volume'
    }, inplace=True)
    df = df[['Open', 'High', 'Low', 'Close', 'Volume']]
    for col in df.columns:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    df.sort_index(inplace=True)

    logger.info(f"  baostock 获取 {symbol} {len(df)} 根")
    return df


def _standardize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """标准化列名和数据格式"""
    column_mapping = {
        '开盘': 'Open',
        '收盘': 'Close',
        '最高': 'High',
        '最低': 'Low',
        '成交量': 'Volume',
    }
    for old_col, new_col in column_mapping.items():
        if old_col in df.columns:
            df.rename(columns={old_col: new_col}, inplace=True)

    if '时间' in df.columns:
        df['时间'] = pd.to_datetime(df['时间'])
        df.set_index('时间', inplace=True)
    elif not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index)

    required = ['Open', 'High', 'Low', 'Close', 'Volume']
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"数据缺少必需列: {missing}")

    df = df[required].copy()
    for col in required:
        df[col] = pd.to_numeric(df[col], errors='coerce')

    df.sort_index(inplace=True)
    return df


# ─── Yahoo Finance 数据拉取（港股/美股）───

def _get_proxies() -> dict:
    """获取代理配置 — 仅用于海外数据源（Yahoo/AlphaVantage 等）。
    优先读取环境变量，否则自动探测本地代理端口。
    注意：国内源已通过 NO_PROXY 环境变量绕过，不受此影响。
    """
    proxy = (os.environ.get('HTTPS_PROXY') or os.environ.get('HTTP_PROXY')
             or os.environ.get('https_proxy') or os.environ.get('http_proxy'))
    if proxy:
        return {'http': proxy, 'https': proxy}

    # 自动探测常见本地代理端口 (Clash:7890, V2Ray:10809, SS:1080)
    import socket
    for port in (7890, 10809, 1080):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(0.3)
            result = s.connect_ex(('127.0.0.1', port))
            s.close()
            if result == 0:
                proxy_url = f'http://127.0.0.1:{port}'
                logger.info(f"自动检测到本地代理: {proxy_url}")
                return {'http': proxy_url, 'https': proxy_url}
        except Exception:
            continue
    return {}


def _to_yahoo_symbol(symbol: str, market: str) -> str:
    """将内部代码转为 Yahoo Finance 代码"""
    if market == 'hk':
        return f"{int(symbol)}.HK"
    return symbol


# ─── Yahoo Session + Crumb 认证 ───
# Yahoo v8 API 需要 cookie + crumb 才能访问（否则返回 403）
_yahoo_session = None
_yahoo_crumb = None
_yahoo_crumb_ts = 0  # crumb 获取时间戳，超过 30 分钟自动刷新


def _get_yahoo_session():
    """获取带有效 cookie + crumb 的 Yahoo session（30 分钟自动刷新）"""
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry

    global _yahoo_session, _yahoo_crumb, _yahoo_crumb_ts

    now = time.monotonic()
    # 如果 session 有效且 crumb 未过期（30 分钟），直接返回
    if _yahoo_session and _yahoo_crumb and (now - _yahoo_crumb_ts) < 1800:
        return _yahoo_session, _yahoo_crumb

    logger.info("  Yahoo: 初始化 session + crumb 认证...")
    session = requests.Session()
    # 仅对 HTTP 状态码重试，连接错误不重试（由外层循环控制）
    retry_strategy = Retry(total=1, backoff_factor=0.5,
                           status_forcelist=[429, 500, 502, 503, 504],
                           raise_on_status=False)
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount('https://', adapter)
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                      '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'application/json,text/html',
        'Accept-Language': 'en-US,en;q=0.9',
    })

    # 显式设置代理到 session（不依赖系统代理，系统代理受 NO_PROXY 影响可能失效）
    proxies = _get_proxies()
    if proxies:
        session.proxies.update(proxies)
        logger.info(f"  Yahoo: session 使用代理 {list(proxies.values())[0]}")

    # Step 1: 获取 Yahoo cookie (fc.yahoo.com 通常返回 404，但 cookie 仍会设置)
    try:
        session.get('https://fc.yahoo.com/', timeout=20, allow_redirects=True)
    except Exception:
        pass

    # Step 2: 获取 crumb（优先 query2，备选 query1，每个端点最多尝试 2 次）
    # 首次通过代理建立连接较慢（~40s），后续请求会复用连接很快
    crumb = None
    for host in ('query2.finance.yahoo.com', 'query1.finance.yahoo.com'):
        for attempt in range(2):
            try:
                r = session.get(f'https://{host}/v1/test/getcrumb', timeout=60)
                if r.status_code == 200 and r.text.strip():
                    crumb = r.text.strip()
                    logger.info(f"  Yahoo: crumb 获取成功 (via {host.split('.')[0]}, attempt {attempt + 1})")
                    break
            except Exception as e:
                logger.debug(f"  Yahoo: crumb from {host} attempt {attempt + 1} failed: {e}")
                continue
        if crumb:
            break

    if not crumb:
        raise ValueError("Yahoo Finance crumb 获取失败（所有端点超时或不可达）")

    _yahoo_session = session
    _yahoo_crumb = crumb
    _yahoo_crumb_ts = now
    return session, crumb


def _parse_yahoo_chart(data: dict, yahoo_symbol: str) -> pd.DataFrame:
    """解析 Yahoo v8 chart API 返回的 JSON 为标准 DataFrame"""
    result = data.get('chart', {}).get('result')
    if not result:
        raise ValueError(f"Yahoo Finance 未返回 {yahoo_symbol} 数据")

    chart = result[0]
    timestamps = chart.get('timestamp')
    if not timestamps:
        raise ValueError(f"Yahoo Finance {yahoo_symbol} 无时间戳数据")
    ohlc = chart['indicators']['quote'][0]

    df = pd.DataFrame({
        'Open': ohlc['open'],
        'High': ohlc['high'],
        'Low': ohlc['low'],
        'Close': ohlc['close'],
        'Volume': ohlc['volume'],
    }, index=pd.to_datetime(timestamps, unit='s'))

    df.index.name = 'datetime'
    for col in ['Open', 'High', 'Low', 'Close', 'Volume']:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    df.dropna(subset=['Open', 'High', 'Low', 'Close'], how='all', inplace=True)
    df.sort_index(inplace=True)
    return df


def _fetch_yahoo(yahoo_symbol: str, period: str = '6mo') -> pd.DataFrame:
    """通过 Yahoo Finance v8 API + crumb 认证获取小时K线（query2 优先）"""
    _throttle('yahoo')
    session, crumb = _get_yahoo_session()

    params = {
        'range': period,
        'interval': '1h',
        'includePrePost': 'false',
        'crumb': crumb,
    }

    # 优先 query2（实测更稳定），备选 query1
    last_err = None
    for host in ('query2.finance.yahoo.com', 'query1.finance.yahoo.com'):
        try:
            url = f'https://{host}/v8/finance/chart/{yahoo_symbol}'
            resp = session.get(url, params=params, timeout=60)
            if resp.status_code == 401 or resp.status_code == 403:
                # crumb 失效，强制刷新
                global _yahoo_crumb_ts
                _yahoo_crumb_ts = 0
                session, crumb = _get_yahoo_session()
                params['crumb'] = crumb
                resp = session.get(url, params=params, timeout=60)
            resp.raise_for_status()
            df = _parse_yahoo_chart(resp.json(), yahoo_symbol)
            logger.info(f"  Yahoo v8 获取 {yahoo_symbol} {len(df)} 根 (via {host.split('.')[0]})")
            return df
        except Exception as e:
            last_err = e
            continue

    raise ValueError(f"Yahoo Finance {yahoo_symbol} 所有端点失败: {last_err}")


def _fetch_yahoo_via_yfinance(yahoo_symbol: str, period: str = '6mo') -> pd.DataFrame:
    """通过 yfinance 库获取小时K线（Yahoo v8 API 备用）"""
    import yfinance as yf

    _throttle('yfinance')
    ticker = yf.Ticker(yahoo_symbol)
    df = ticker.history(period=period, interval='1h')

    if df is None or df.empty:
        raise ValueError(f"yfinance 未返回 {yahoo_symbol} 数据")

    # yfinance 返回的列名可能是首字母大写也可能小写，统一处理
    col_map = {}
    for col in df.columns:
        if col.lower() == 'open':
            col_map[col] = 'Open'
        elif col.lower() == 'high':
            col_map[col] = 'High'
        elif col.lower() == 'low':
            col_map[col] = 'Low'
        elif col.lower() == 'close':
            col_map[col] = 'Close'
        elif col.lower() == 'volume':
            col_map[col] = 'Volume'
    if col_map:
        df.rename(columns=col_map, inplace=True)

    df = df[['Open', 'High', 'Low', 'Close', 'Volume']]
    df.index.name = 'datetime'
    for col in df.columns:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    df.dropna(subset=['Open', 'High', 'Low', 'Close'], how='all', inplace=True)
    df.sort_index(inplace=True)

    logger.info(f"  yfinance 获取 {yahoo_symbol} {len(df)} 根")
    return df


def _fetch_via_alphavantage(symbol: str, market: str, period: str = '6mo') -> pd.DataFrame:
    """通过 Alpha Vantage 获取小时K线（免费API Key, 25次/天）
    需设置环境变量 ALPHAVANTAGE_API_KEY 或在 .env 中配置。
    """
    import requests

    api_key = os.environ.get('ALPHAVANTAGE_API_KEY', '').strip()
    if not api_key:
        raise ValueError("未配置 ALPHAVANTAGE_API_KEY 环境变量 (免费申请: https://www.alphavantage.co/support/#api-key)")

    _throttle('alphavantage')

    # Alpha Vantage 用原始美股代码，港股需要加 .HKG 后缀
    if market == 'hk':
        av_symbol = f"{int(symbol):04d}.HKG"
    else:
        av_symbol = symbol

    # outputsize: compact=最近100条, full=全部
    outputsize = 'full' if period in ('6mo', '1y', 'max') else 'compact'

    url = 'https://www.alphavantage.co/query'
    params = {
        'function': 'TIME_SERIES_INTRADAY',
        'symbol': av_symbol,
        'interval': '60min',
        'outputsize': outputsize,
        'apikey': api_key,
    }
    proxies = _get_proxies()

    resp = requests.get(url, params=params, headers={'User-Agent': 'Mozilla/5.0'},
                        timeout=30, proxies=proxies)
    resp.raise_for_status()
    data = resp.json()

    # 检查错误
    if 'Error Message' in data:
        raise ValueError(f"Alpha Vantage: {data['Error Message']}")
    if 'Note' in data:
        raise ValueError(f"Alpha Vantage 限流: {data['Note']}")

    ts_key = 'Time Series (60min)'
    if ts_key not in data:
        raise ValueError(f"Alpha Vantage 未返回 {symbol} 的小时数据, keys={list(data.keys())}")

    ts = data[ts_key]
    rows = []
    for dt_str, vals in ts.items():
        rows.append({
            'datetime': dt_str,
            'Open': float(vals.get('1. open', 0)),
            'High': float(vals.get('2. high', 0)),
            'Low': float(vals.get('3. low', 0)),
            'Close': float(vals.get('4. close', 0)),
            'Volume': float(vals.get('5. volume', 0)),
        })

    if not rows:
        raise ValueError(f"Alpha Vantage {symbol} 解析后无数据")

    df = pd.DataFrame(rows)
    df['datetime'] = pd.to_datetime(df['datetime'])
    df.set_index('datetime', inplace=True)
    df.sort_index(inplace=True)
    df.dropna(subset=['Open', 'High', 'Low', 'Close'], how='all', inplace=True)

    logger.info(f"  Alpha Vantage 获取 {symbol} {len(df)} 根小时线")
    return df


def _fetch_via_efinance_intl(symbol: str, market: str) -> pd.DataFrame:
    """通过 efinance 获取港股/美股小时K线"""
    import efinance as ef

    _throttle('efinance')
    df = ef.stock.get_quote_history(symbol, klt=60, fqt=1)

    if df is None or df.empty:
        raise ValueError(f"efinance 未返回 {symbol} 的数据")

    df = _standardize_columns(df)
    logger.info(f"  efinance(intl) 获取 {symbol} {len(df)} 根")
    return df


def _get_twelvedata_key() -> str:
    """从 SQLite settings 表读取 Twelve Data API Key"""
    try:
        import sqlite3
        db_path = _get_data_dir() / 'zqtrade.db'
        if not db_path.exists():
            return None
        conn = sqlite3.connect(str(db_path))
        row = conn.execute(
            "SELECT value FROM settings WHERE key='twelvedata_api_key'"
        ).fetchone()
        conn.close()
        return row[0] if row and row[0] else None
    except Exception:
        return None


def _fetch_via_twelvedata(symbol: str, market: str, period: str = '6mo') -> pd.DataFrame:
    """通过 Twelve Data API 获取港股/美股小时K线（需要 API Key）"""
    import requests

    api_key = _get_twelvedata_key()
    if not api_key:
        raise ValueError("Twelve Data API Key 未配置")

    _throttle('twelvedata')

    if market == 'hk':
        td_symbol = f"{int(symbol):04d}:HKEX"
    else:
        td_symbol = symbol

    period_to_size = {'5d': 35, '1mo': 150, '3mo': 450, '6mo': 900}
    outputsize = period_to_size.get(period, 500)

    url = 'https://api.twelvedata.com/time_series'
    params = {
        'symbol': td_symbol,
        'interval': '1h',
        'outputsize': outputsize,
        'apikey': api_key,
    }

    resp = requests.get(url, params=params, timeout=20)
    resp.raise_for_status()
    data = resp.json()

    if data.get('status') == 'error':
        raise ValueError(f"Twelve Data: {data.get('message', 'unknown error')}")

    values = data.get('values', [])
    if not values:
        raise ValueError(f"Twelve Data 未返回 {symbol} 的数据")

    df = pd.DataFrame(values)
    df['datetime'] = pd.to_datetime(df['datetime'])
    df.set_index('datetime', inplace=True)
    df.rename(columns={
        'open': 'Open', 'high': 'High', 'low': 'Low',
        'close': 'Close', 'volume': 'Volume'
    }, inplace=True)
    df = df[['Open', 'High', 'Low', 'Close', 'Volume']]
    for col in df.columns:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    df.sort_index(inplace=True)

    logger.info(f"  Twelve Data 获取 {symbol} {len(df)} 根")
    return df


# ─── 东财港美股小时线（国内源）───

# 东财美股交易所代码缓存：symbol -> '105' / '106'
_US_EXCHANGE_CACHE = {}


def _fetch_via_em_us_hourly(symbol: str) -> pd.DataFrame:
    """通过东财(akshare)获取美股小时K线 — 1分钟数据重采样为60分钟
    自动尝试 106(纽交所) 和 105(纳斯达克) 两个前缀。
    """
    import akshare as ak

    _throttle('akshare_em')

    # 优先使用缓存的前缀
    prefixes = ['106', '105']
    if symbol in _US_EXCHANGE_CACHE:
        cached = _US_EXCHANGE_CACHE[symbol]
        prefixes = [cached] + [p for p in prefixes if p != cached]

    df = None
    last_err = None
    for prefix in prefixes:
        em_symbol = f"{prefix}.{symbol}"
        try:
            df = ak.stock_us_hist_min_em(symbol=em_symbol)
            if df is not None and not df.empty:
                _US_EXCHANGE_CACHE[symbol] = prefix
                break
            df = None
        except Exception as e:
            last_err = e
            df = None
            continue

    if df is None or df.empty:
        raise ValueError(f"东财未返回 {symbol} 的分钟数据 (尝试了 105/106): {last_err}")

    # 统一列名 (东财中文列: 时间, 开盘, 收盘, 最高, 最低, 成交量, 成交额, 最新价)
    col_map = {}
    for col in df.columns:
        cl = col.lower() if isinstance(col, str) else ''
        if '时间' in str(col) or 'time' in cl or 'date' in cl:
            col_map[col] = 'datetime'
        elif '开盘' in str(col) or col == 'open':
            col_map[col] = 'Open'
        elif '收盘' in str(col) or col == 'close':
            col_map[col] = 'Close'
        elif '最高' in str(col) or col == 'high':
            col_map[col] = 'High'
        elif '最低' in str(col) or col == 'low':
            col_map[col] = 'Low'
        elif '成交量' in str(col) or col == 'volume':
            col_map[col] = 'Volume'
    df.rename(columns=col_map, inplace=True)

    if 'datetime' not in df.columns:
        raise ValueError(f"东财美股数据缺少时间列, 实际列: {list(df.columns)}")

    df['datetime'] = pd.to_datetime(df['datetime'])
    df.set_index('datetime', inplace=True)

    for col in ['Open', 'High', 'Low', 'Close', 'Volume']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')

    # 1分钟 → 60分钟重采样
    hourly = df.resample('1h').agg({
        'Open': 'first', 'High': 'max', 'Low': 'min',
        'Close': 'last', 'Volume': 'sum'
    }).dropna(subset=['Open', 'High', 'Low', 'Close'])

    if hourly.empty:
        raise ValueError(f"东财美股 {symbol} 重采样后无数据")

    hourly.sort_index(inplace=True)
    logger.info(f"  东财(EM) 美股获取 {symbol} {len(df)} 根1分钟, 重采样为 {len(hourly)} 根小时线")
    return hourly


def _fetch_via_em_hk_hourly(symbol: str) -> pd.DataFrame:
    """通过东财(akshare)获取港股小时K线"""
    import akshare as ak

    _throttle('akshare_em')

    hk_symbol = symbol.zfill(5)
    df = ak.stock_hk_hist_min_em(symbol=hk_symbol, period='60', adjust='qfq')

    if df is None or df.empty:
        raise ValueError(f"东财未返回 {hk_symbol} 的小时数据")

    # 统一列名
    col_map = {}
    for col in df.columns:
        if '时间' in str(col):
            col_map[col] = 'datetime'
        elif '开盘' in str(col):
            col_map[col] = 'Open'
        elif '收盘' in str(col):
            col_map[col] = 'Close'
        elif '最高' in str(col):
            col_map[col] = 'High'
        elif '最低' in str(col):
            col_map[col] = 'Low'
        elif '成交量' in str(col):
            col_map[col] = 'Volume'
    df.rename(columns=col_map, inplace=True)

    if 'datetime' not in df.columns:
        raise ValueError(f"东财港股数据缺少时间列, 实际列: {list(df.columns)}")

    df['datetime'] = pd.to_datetime(df['datetime'])
    df.set_index('datetime', inplace=True)

    required = ['Open', 'High', 'Low', 'Close', 'Volume']
    for col in required:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')

    df = df[required]
    df.dropna(subset=['Open', 'High', 'Low', 'Close'], how='all', inplace=True)
    df.sort_index(inplace=True)

    logger.info(f"  东财(EM) 港股获取 {hk_symbol} {len(df)} 根小时线")
    return df


def _fetch_hk_us(yahoo_symbol: str, period: str = '6mo',
                 symbol: str = None, market: str = None) -> pd.DataFrame:
    """港股/美股小时K线获取 — 多源轮换（国内源优先，Yahoo兜底）"""
    orig_sym = symbol or yahoo_symbol.replace('.HK', '')
    mkt = market or 'us'

    # 国内源优先 → Yahoo多端点 → 国际备用
    sources = []

    # 1) 东财小时线（国内，稳定）
    if mkt == 'hk':
        sources.append(('akshare_em', lambda: _fetch_via_em_hk_hourly(orig_sym)))
    else:
        sources.append(('akshare_em', lambda: _fetch_via_em_us_hourly(orig_sym)))

    # 2) efinance（东财另一端点）
    if _check_lib('efinance'):
        sources.append(('efinance', lambda: _fetch_via_efinance_intl(orig_sym, mkt)))

    # 3) Yahoo（session + crumb 认证，内部自动尝试 query2/query1）
    sources.append(('yahoo', lambda: _fetch_yahoo(yahoo_symbol, period=period)))

    # 4) yfinance 库
    sources.append(('yfinance', lambda: _fetch_yahoo_via_yfinance(yahoo_symbol, period=period)))

    # 5) Alpha Vantage（需免费API Key）
    if os.environ.get('ALPHAVANTAGE_API_KEY', '').strip():
        sources.append(('alphavantage', lambda: _fetch_via_alphavantage(orig_sym, mkt, period)))

    # 6) Twelve Data（需API Key）
    if _check_lib('twelvedata'):
        sources.append(('twelvedata', lambda: _fetch_via_twelvedata(orig_sym, mkt, period)))

    try:
        source_name, df = _try_sources(sources)
        # 统一去掉时区信息（yfinance 返回 America/New_York 时区，与无时区 datetime 比较会报错）
        if df.index.tz is not None:
            df.index = df.index.tz_localize(None)
        logger.info(f"  [{mkt.upper()}] {orig_sym} 通过 {source_name} 获取 {len(df)} 根小时线")
        return df
    except Exception as e:
        logger.error(f"  [{mkt.upper()}] {orig_sym} 所有小时线数据源均失败: {e}")
        return None


def load_from_csv(filepath: str) -> pd.DataFrame:
    """从 CSV 文件加载标准 OHLCV 数据"""
    df = pd.read_csv(filepath, index_col=0, parse_dates=True)
    required = ['Open', 'High', 'Low', 'Close', 'Volume']
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"CSV文件缺少必需列: {missing}")
    df = df[required]
    for col in required:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    df.sort_index(inplace=True)
    # 统一去掉时区信息（避免与无时区 datetime 比较报错）
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    return df


def save_to_csv(df: pd.DataFrame, filepath: str):
    """保存 DataFrame 到 CSV"""
    os.makedirs(os.path.dirname(filepath) if os.path.dirname(filepath) else '.', exist_ok=True)
    df.to_csv(filepath)


# ─── 股票名称 ───

def get_stock_name(symbol: str) -> str:
    """获取股票名称。A股用Sina，港股/美股用Yahoo Finance。"""
    market = detect_market(symbol)
    if market == 'cn':
        return _get_cn_stock_name(symbol)
    else:
        return _get_yahoo_stock_name(symbol, market)


def _get_cn_stock_name(symbol: str) -> str:
    """通过 Sina Finance 获取A股名称"""
    try:
        import requests
        _throttle('sina')
        market = 'sh' if symbol.startswith('6') else 'sz'
        url = f"https://hq.sinajs.cn/list={market}{symbol}"
        headers = {'Referer': 'https://finance.sina.com.cn'}
        resp = requests.get(url, headers=headers, timeout=5)
        resp.encoding = 'gbk'
        text = resp.text
        if '="' in text:
            content = text.split('="')[1]
            name = content.split(',')[0]
            if name:
                return name
    except Exception:
        pass
    return ""


def _get_yahoo_stock_name(symbol: str, market: str) -> str:
    """通过多渠道获取港股/美股名称（Sina优先，Yahoo session+crumb 备用）"""
    yahoo_sym = _to_yahoo_symbol(symbol, market)

    # 1) Sina Finance（国内源，速度快、无代理问题）
    try:
        import requests
        headers = {'Referer': 'https://finance.sina.com.cn', 'User-Agent': 'Mozilla/5.0'}
        if market == 'us':
            url = f"https://hq.sinajs.cn/list=gb_{symbol.lower()}"
            resp = requests.get(url, headers=headers, timeout=10)
            resp.encoding = 'gbk'
            text = resp.text
            if '="' in text:
                fields = text.split('="')[1].split(',')
                if fields and fields[0]:
                    name = fields[0].strip('"')
                    if name:
                        return name
        elif market == 'hk':
            url = f"https://hq.sinajs.cn/list=rt_hk{symbol.zfill(5)}"
            resp = requests.get(url, headers=headers, timeout=10)
            resp.encoding = 'gbk'
            text = resp.text
            if '="' in text:
                fields = text.split('="')[1].split(',')
                if len(fields) > 1 and fields[1]:
                    name = fields[1].strip('"')
                    if name:
                        return name
    except Exception:
        pass

    # 2) Yahoo session + crumb 认证
    try:
        _throttle('yahoo')
        session, crumb = _get_yahoo_session()
        for host in ('query2.finance.yahoo.com', 'query1.finance.yahoo.com'):
            try:
                url = f'https://{host}/v8/finance/chart/{yahoo_sym}'
                resp = session.get(url, params={'range': '1d', 'interval': '1d', 'crumb': crumb}, timeout=60)
                if resp.status_code == 200:
                    data = resp.json()
                    result = data.get('chart', {}).get('result')
                    if result:
                        meta = result[0].get('meta', {})
                        name = meta.get('longName') or meta.get('shortName') or ''
                        if name:
                            return name
            except Exception:
                continue
    except Exception:
        pass

    # 3) yfinance 库兜底
    try:
        import yfinance as yf
        _throttle('yfinance')
        ticker = yf.Ticker(yahoo_sym)
        info = getattr(ticker, 'info', {}) or {}
        name = info.get('longName') or info.get('shortName') or ''
        if name:
            return name
    except Exception:
        pass

    return ""
