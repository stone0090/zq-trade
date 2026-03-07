"""
数据获取模块

支持 A 股小时级 K 线数据的智能增量获取与本地缓存。
通过股票代码自动识别市场，按市场分目录缓存。
"""
import os
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path


# ─── 每日K线根数（用于从目标根数反推天数）───

_BARS_PER_DAY = {
    'cn': 4,    # A股：10:30, 11:30, 14:00, 15:00
    'us': 7,    # 美股盘中：预留
}


# ─── 市场识别 ───

def detect_market(symbol: str) -> str:
    """根据代码格式识别市场: 纯数字 → 'cn'，含字母 → 'us'"""
    return 'cn' if symbol.isdigit() else 'us'


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
                      bars: int = 500) -> pd.DataFrame:
    """
    智能增量获取小时K线数据。

    1. 根据截止日期和目标根数，计算需要的时间范围
    2. 读取本地缓存，判断是否需要增量拉取
    3. 增量拉取后与本地合并、去重、保存
    4. 返回截止日期前最近 bars 根数据

    Args:
        symbol: 股票代码（纯数字=A股，含字母=美股）
        end_date: 截止日期 'YYYY-MM-DD'，默认今天
        bars: 目标K线根数，默认500

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
    else:
        end_dt = end_dt.replace(hour=16, minute=0, second=0, microsecond=0)

    # 从目标根数反推需要的自然天数（加余量）
    bars_per_day = _BARS_PER_DAY.get(market, 4)
    trading_days_needed = (bars // bars_per_day) + 10  # 余量
    calendar_days = int(trading_days_needed * 1.5)     # 交易日→自然日
    start_dt = end_dt - timedelta(days=calendar_days)

    cache_file = _cache_path(symbol)

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

        # 本地数据已覆盖截止日期，直接截取
        if local_end >= end_dt:
            result = local_df[local_df.index <= end_dt].tail(bars)
            print(f"使用本地缓存: {cache_file} ({len(local_df)}根)")
            print(f"  截取 {len(result)} 根 (截至 {end_dt.strftime('%Y-%m-%d')})")
            return result

        # 本地数据不够新 → 判断是否需要增量拉取
        # 如果差距很小（3天内），可能是周末/节假日，尝试增量但容忍失败
        gap_days = (end_dt - local_end).days
        incr_start = local_end + timedelta(hours=1)
        incr_start_str = incr_start.strftime('%Y-%m-%d %H:%M:%S')
        end_str = end_dt.strftime('%Y-%m-%d %H:%M:%S')

        print(f"本地缓存截至 {local_end.strftime('%Y-%m-%d %H:%M')}，增量拉取...")
        try:
            new_df = _fetch_cn(symbol, incr_start_str, end_str)
            if new_df is not None and not new_df.empty:
                merged = pd.concat([local_df, new_df])
                merged = merged[~merged.index.duplicated(keep='last')].sort_index()
                print(f"  增量获取 {len(new_df)} 根，合并后共 {len(merged)} 根")
            else:
                merged = local_df
                print(f"  无新增数据，使用本地 {len(merged)} 根")
        except Exception as e:
            print(f"  增量拉取失败: {e}，使用本地数据")
            merged = local_df

        # 同时检查是否需要往前补数据
        if local_start > start_dt:
            prepend_end_str = (local_start - timedelta(hours=1)).strftime('%Y-%m-%d %H:%M:%S')
            prepend_start_str = start_dt.strftime('%Y-%m-%d %H:%M:%S')
            print(f"  向前补充数据: {prepend_start_str} ~ {prepend_end_str}")
            try:
                old_df = _fetch_cn(symbol, prepend_start_str, prepend_end_str)
                if old_df is not None and not old_df.empty:
                    merged = pd.concat([old_df, merged])
                    merged = merged[~merged.index.duplicated(keep='last')].sort_index()
                    print(f"  向前补充 {len(old_df)} 根，合并后共 {len(merged)} 根")
            except Exception as e:
                print(f"  向前补充失败: {e}")
    else:
        # 无本地缓存 → 全量拉取
        start_str = start_dt.strftime('%Y-%m-%d %H:%M:%S')
        end_str = end_dt.strftime('%Y-%m-%d %H:%M:%S')
        print(f"无本地缓存，全量拉取 {symbol}...")
        merged = _fetch_cn(symbol, start_str, end_str)

    if merged is None or merged.empty:
        raise ValueError(f"未能获取到 {symbol} 的有效数据")

    # 保存合并后的完整数据
    save_to_csv(merged, str(cache_file))

    # 截取截止日期前最近 bars 根
    result = merged[merged.index <= end_dt].tail(bars)
    print(f"数据就绪: {len(result)} 根小时K线")
    if len(result) > 0:
        print(f"  范围: {result.index[0]} ~ {result.index[-1]}")

    return result


# ─── A股数据拉取 ───

def _fetch_cn(symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
    """拉取A股小时K线，akshare优先，Sina备用"""
    ak_err = None
    # 先尝试 akshare (eastmoney)
    try:
        df = _fetch_via_akshare(symbol, start_date, end_date)
        return df
    except Exception as e:
        ak_err = str(e)
        print(f"  akshare 获取失败: {ak_err}")
        print("  尝试备用数据源 (Sina Finance)...")

    # 回退到 Sina Finance API
    try:
        # 根据日期范围计算需要的datalen
        start_dt = pd.to_datetime(start_date)
        end_dt = pd.to_datetime(end_date)
        days_span = (end_dt - start_dt).days
        needed_datalen = max(1500, days_span * 4)  # A股每天4根，加余量
        df = _fetch_via_sina(symbol, datalen=needed_datalen)
        if df is not None and not df.empty:
            df = df[(df.index >= start_dt) & (df.index <= end_dt)]
            if not df.empty:
                print(f"  Sina Finance 获取 {len(df)} 根")
                return df
        raise ValueError("Sina Finance 未返回有效数据")
    except Exception as e2:
        raise Exception(f"所有数据源均失败。akshare: {ak_err} | Sina: {e2}")


def _fetch_via_akshare(symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
    """通过 akshare (eastmoney) 获取数据"""
    try:
        import akshare as ak
    except ImportError:
        raise ImportError("请先安装 akshare: pip install akshare")

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


# ─── CSV 读写 ───

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
    return df


def save_to_csv(df: pd.DataFrame, filepath: str):
    """保存 DataFrame 到 CSV"""
    os.makedirs(os.path.dirname(filepath) if os.path.dirname(filepath) else '.', exist_ok=True)
    df.to_csv(filepath)


# ─── 股票名称 ───

def get_stock_name(symbol: str) -> str:
    """
    获取股票名称。

    通过 Sina Finance 实时行情接口获取，失败时返回空字符串。
    """
    try:
        import requests
        market = 'sh' if symbol.startswith('6') else 'sz'
        url = f"https://hq.sinajs.cn/list={market}{symbol}"
        headers = {'Referer': 'https://finance.sina.com.cn'}
        resp = requests.get(url, headers=headers, timeout=5)
        resp.encoding = 'gbk'
        text = resp.text
        # 格式: var hq_str_sh600802="福建水泥,6.55,...";
        if '="' in text:
            content = text.split('="')[1]
            name = content.split(',')[0]
            if name:
                return name
    except Exception:
        pass
    return ""
