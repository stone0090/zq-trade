"""
DL 独立结构检测

从最新数据往前回溯，识别水平盘整区间（independent structure），
验证其K线数量是否满足 ≥ 60 根的硬性条件。
"""
import numpy as np
import pandas as pd

from src.analyzer.base import AnalyzerConfig, StructureResult, PassFail
from src.utils.helpers import (
    linear_regression_slope, normalize_slope, calc_atr
)


def analyze_structure(df: pd.DataFrame, config: AnalyzerConfig = None) -> StructureResult:
    """
    DL 独立结构分析。

    算法:
    1. 滑动窗口线性回归 → 计算每段斜率
    2. 斜率 < 阈值 → 标记为盘整段
    3. 从尾部往前找最近的连续盘整区间
    4. 验证K线数量、前趋势、结构倾斜度
    """
    if config is None:
        config = AnalyzerConfig()

    result = StructureResult()

    if len(df) < config.dl_window_size + 10:
        result.reasoning.append(f"数据不足: 仅有 {len(df)} 根K线，无法分析")
        return result

    close = df['Close'].values
    window = config.dl_window_size
    n = len(close)

    # ─── 1. 计算每个窗口的归一化斜率 ───
    slopes = np.full(n, np.nan)
    for i in range(window - 1, n):
        segment = close[i - window + 1: i + 1]
        if np.isnan(segment).any():
            continue
        x = np.arange(window, dtype=float)
        coeffs = np.polyfit(x, segment, 1)
        mean_price = segment.mean()
        if mean_price != 0:
            slopes[i] = abs(coeffs[0] / mean_price * 100)
        else:
            slopes[i] = 0.0

    # ─── 2. 标记盘整段 vs 趋势段 ───
    is_flat = np.array([
        s <= config.dl_flat_slope_threshold if not np.isnan(s) else False
        for s in slopes
    ])

    # ─── 3. 从尾部往前找最近的连续盘整区间 ───
    # 策略: 先跳过尾部的趋势段（突破区），再找盘整段
    # 步骤a: 从尾部往前，跳过非flat区，找到第一个flat窗口作为structure_end
    # 步骤b: 从structure_end往前，扩展盘整区间（允许少量噪声）

    # 步骤a: 找到盘整区间的右边界
    structure_end = -1
    i = n - 1
    while i >= window - 1:
        if is_flat[i]:
            structure_end = i
            break
        i -= 1

    if structure_end < 0:
        # 整段数据都是趋势，无盘整
        result.reasoning.append("未检测到有效盘整区间（全为趋势段）")
        return result

    # 步骤b: 从 structure_end 往前扩展盘整区间
    structure_start = structure_end
    current_noise_streak = 0

    i = structure_end - 1
    while i >= window - 1:
        if is_flat[i]:
            structure_start = i
            current_noise_streak = 0
        else:
            current_noise_streak += 1
            if current_noise_streak > config.dl_noise_tolerance:
                # 噪声过多，盘整段到此为止
                break
        i -= 1

    # 将窗口索引映射到实际K线索引
    # structure_start 对应的窗口覆盖 [structure_start - window + 1, structure_start]
    structure_start_kline = max(0, structure_start - window + 1)
    structure_end_kline = structure_end

    # 更新变量名以保持后续代码一致
    structure_start = structure_start_kline
    structure_end = structure_end_kline

    # ─── 4. 盘整区间边界和K线统计 ───
    struct_df = df.iloc[structure_start: structure_end + 1]
    kline_count = len(struct_df)

    if kline_count < 10:
        result.reasoning.append(f"未检测到有效盘整区间（仅 {kline_count} 根K线）")
        return result

    # 上下边界: 使用分位数排除极端影线
    highs = struct_df['High'].values
    lows = struct_df['Low'].values
    range_high = float(np.percentile(highs, 95))
    range_low = float(np.percentile(lows, 5))
    mean_price = struct_df['Close'].mean()
    range_pct = (range_high - range_low) / mean_price * 100 if mean_price > 0 else 0

    result.kline_count = kline_count
    result.range_high = range_high
    result.range_low = range_low
    result.range_pct = round(range_pct, 2)
    result.structure_start_idx = structure_start
    result.structure_end_idx = structure_end

    # ─── 5. 缺陷检测 ───

    # 5a. 前趋势急跌检测
    pre_trend_len = min(30, structure_start)
    if pre_trend_len >= 5:
        pre_close = df['Close'].iloc[structure_start - pre_trend_len: structure_start]
        pre_slope = linear_regression_slope(pre_close)
        pre_slope_norm = normalize_slope(pre_slope, pre_close.mean())
        result.prior_trend_slope = round(pre_slope_norm, 4)

        if pre_slope < 0 and pre_slope_norm > config.dl_steep_decline_threshold:
            result.flaws.append(f"结构前有急跌（斜率 {pre_slope_norm:.3f}%/K线）")
    else:
        result.prior_trend_slope = 0.0

    # 5b. 结构右倾检测
    struct_slope = linear_regression_slope(struct_df['Close'])
    struct_slope_norm = normalize_slope(struct_slope, mean_price)
    result.structure_slope = round(struct_slope_norm, 4)

    if struct_slope > 0 and struct_slope_norm > config.dl_tilt_threshold:
        result.flaws.append(f"结构向右上倾斜（斜率 {struct_slope_norm:.3f}%/K线）")

    # ─── 6. 评分 ───
    if kline_count >= config.dl_min_klines:
        result.score = PassFail.S
        result.passed = True
        result.reasoning.append(f"盘整区间包含 {kline_count} 根K线（≥{config.dl_min_klines}），结构充分")
    elif kline_count >= config.dl_min_klines_relaxed:
        # 经验覆盖: 筹码集中度检验
        price_std = struct_df['Close'].std()
        concentration = price_std / mean_price if mean_price > 0 else 1.0
        if concentration < config.dl_concentration_threshold:
            result.score = PassFail.S
            result.passed = True
            result.reasoning.append(
                f"盘整区间 {kline_count} 根K线（<{config.dl_min_klines}），"
                f"但筹码集中度优异（{concentration:.4f} < {config.dl_concentration_threshold}），经验放行"
            )
        else:
            result.reasoning.append(
                f"盘整区间仅 {kline_count} 根K线，"
                f"且筹码集中度不足（{concentration:.4f}），不满足条件"
            )
    else:
        result.reasoning.append(f"盘整区间仅 {kline_count} 根K线，远不足 {config.dl_min_klines} 根要求")

    # 补充区间信息
    result.reasoning.append(f"区间价格: {range_low:.2f} ~ {range_high:.2f}，振幅 {range_pct:.2f}%")

    return result
