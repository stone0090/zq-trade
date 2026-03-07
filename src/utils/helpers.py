"""
公用工具函数

提供 ATR 计算、线性回归、价格聚类等被多个分析器共用的基础函数。
"""
import numpy as np
import pandas as pd


def calc_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """计算 Average True Range"""
    high = df['High']
    low = df['Low']
    close = df['Close']
    prev_close = close.shift(1)

    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()

    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return true_range.rolling(window=period, min_periods=1).mean()


def linear_regression_slope(series: pd.Series) -> float:
    """对 Series 做线性回归，返回斜率。series 可以含 NaN（自动过滤）。"""
    values = series.dropna().values
    if len(values) < 2:
        return 0.0
    x = np.arange(len(values), dtype=float)
    coeffs = np.polyfit(x, values, 1)
    return float(coeffs[0])


def normalize_slope(slope: float, mean_price: float) -> float:
    """将斜率归一化为 %/K线（每根K线价格变化的百分比）"""
    if mean_price == 0:
        return 0.0
    return abs(slope / mean_price * 100)


def price_clustering(prices: np.ndarray, bin_width: float, top_n: int = 5) -> list:
    """
    价格聚类：将价格按 bin_width 分箱，返回频次最高的 top_n 个 bin 中心。

    Returns:
        list of (center_price, count) 按 count 降序
    """
    if len(prices) == 0 or bin_width <= 0:
        return []

    price_min = prices.min()
    price_max = prices.max()

    bins = np.arange(price_min - bin_width, price_max + 2 * bin_width, bin_width)
    if len(bins) < 2:
        return [(float(prices.mean()), len(prices))]

    counts, edges = np.histogram(prices, bins=bins)
    centers = (edges[:-1] + edges[1:]) / 2

    # 按频次降序排列
    indices = np.argsort(counts)[::-1]
    results = []
    for i in indices[:top_n]:
        if counts[i] > 0:
            results.append((float(centers[i]), int(counts[i])))

    return results


def candle_body(open_price: float, close_price: float) -> tuple:
    """返回 (body_low, body_high)"""
    return (min(open_price, close_price), max(open_price, close_price))


def candle_range(row) -> float:
    """计算单根K线振幅"""
    return row['High'] - row['Low']


def candle_body_size(row) -> float:
    """计算单根K线实体大小"""
    return abs(row['Close'] - row['Open'])


def is_bullish(row) -> bool:
    """判断是否为阳线"""
    return row['Close'] > row['Open']


def rolling_slope_series(close: pd.Series, window: int = 20) -> pd.Series:
    """
    计算滑动窗口内的线性回归斜率序列。
    返回的每个值是对应窗口内的归一化斜率（%/K线）。
    """
    slopes = pd.Series(index=close.index, dtype=float)

    values = close.values
    for i in range(window - 1, len(values)):
        segment = values[i - window + 1: i + 1]
        if np.isnan(segment).any():
            slopes.iloc[i] = np.nan
            continue
        x = np.arange(window, dtype=float)
        coeffs = np.polyfit(x, segment, 1)
        mean_price = segment.mean()
        if mean_price != 0:
            slopes.iloc[i] = abs(coeffs[0] / mean_price * 100)
        else:
            slopes.iloc[i] = 0.0

    return slopes


def find_local_extremes(series: pd.Series, order: int = 5) -> tuple:
    """
    找局部极值点。

    Args:
        series: 价格序列
        order: 前后各需要 order 个点都比当前点小/大

    Returns:
        (local_max_indices, local_min_indices) 两个列表
    """
    values = series.values
    n = len(values)
    local_max = []
    local_min = []

    for i in range(order, n - order):
        if np.isnan(values[i]):
            continue
        window = values[i - order: i + order + 1]
        if np.isnan(window).any():
            continue
        if values[i] == window.max():
            local_max.append(i)
        if values[i] == window.min():
            local_min.append(i)

    return local_max, local_min


def clean_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """清洗 OHLCV 数据：去除全 NaN 行，前向填充少量缺失值。"""
    df = df.copy()
    # 去除 OHLCV 全为 NaN 的行
    ohlcv_cols = ['Open', 'High', 'Low', 'Close', 'Volume']
    df = df.dropna(subset=ohlcv_cols, how='all')
    # 前向填充少量缺失
    df[ohlcv_cols] = df[ohlcv_cols].ffill()
    return df
