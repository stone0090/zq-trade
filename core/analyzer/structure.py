"""
DL 独立结构检测

从最新数据往前回溯，识别水平盘整区间（independent structure），
验证其K线数量是否满足 ≥ 90 根的硬性条件。
"""
import numpy as np
import pandas as pd

from core.types import AnalyzerConfig, StructureResult, GradeScore
from core.utils.helpers import (
    linear_regression_slope, calc_atr
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
    steep_threshold = config.dl_flat_slope_threshold * 3  # 明显趋势段阈值

    i = structure_end - 1
    while i >= window - 1:
        if is_flat[i]:
            structure_start = i
            current_noise_streak = 0
        else:
            # 遇到明显趋势段（斜率远超盘整阈值）→ 立即截断
            if not np.isnan(slopes[i]) and slopes[i] > steep_threshold:
                break
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

    # ─── 4b. 端点漂移检查：包含趋势段时收窄结构 ───
    _narrow_if_drifting(df, result, config)

    # 收窄后更新局部变量（_narrow_if_drifting可能修改了result的字段）
    kline_count = result.kline_count
    structure_start = result.structure_start_idx
    range_high = result.range_high
    range_low = result.range_low
    range_pct = result.range_pct
    struct_df = df.iloc[structure_start: structure_end + 1]
    mean_price = struct_df['Close'].mean()

    # ─── 5. 缺陷检测 ───

    # 5a. 前趋势急跌/急涨检测
    pre_trend_len = min(30, structure_start)
    if pre_trend_len >= 5:
        pre_close = df['Close'].iloc[structure_start - pre_trend_len: structure_start]
        pre_slope = linear_regression_slope(pre_close)
        pre_mean = pre_close.mean()
        pre_slope_signed = pre_slope / pre_mean * 100 if pre_mean != 0 else 0.0
        result.prior_trend_slope = round(pre_slope_signed, 4)

        if pre_slope_signed < -config.dl_steep_decline_threshold:
            result.flaws.append(f"结构前有急跌（斜率 {pre_slope_signed:.3f}%/K线）")
        elif pre_slope_signed > config.dl_steep_decline_threshold:
            result.flaws.append(f"结构前有急涨（斜率 {pre_slope_signed:.3f}%/K线）")
    else:
        result.prior_trend_slope = 0.0

    # 5b. 结构倾斜检测
    struct_slope = linear_regression_slope(struct_df['Close'])
    struct_slope_signed = struct_slope / mean_price * 100 if mean_price != 0 else 0.0
    result.structure_slope = round(struct_slope_signed, 4)

    if struct_slope_signed > config.dl_tilt_threshold:
        result.flaws.append(f"结构向右上倾斜（斜率 {struct_slope_signed:.3f}%/K线）")
    elif struct_slope_signed < -config.dl_tilt_threshold:
        result.flaws.append(f"结构向右下倾斜（斜率 {struct_slope_signed:.3f}%/K线）")

    # ─── 6. 评分（初始评分，方向性限制在scorer中处理） ───
    if kline_count >= config.dl_min_klines:
        result.score = GradeScore.S
        result.passed = True
        result.reasoning.append(f"盘整区间包含 {kline_count} 根K线（≥{config.dl_min_klines}），结构充分")
    else:
        result.score = GradeScore.C
        result.passed = False
        result.reasoning.append(
            f"盘整区间 {kline_count} 根K线（<{config.dl_min_klines}），"
            f"结构未成熟，可继续观察"
        )

    # 补充区间信息
    result.reasoning.append(f"区间价格: {range_low:.2f} ~ {range_high:.2f}，振幅 {range_pct:.2f}%")

    return result


def _narrow_if_drifting(df: pd.DataFrame, result: StructureResult,
                        config: AnalyzerConfig):
    """
    如果结构振幅过宽或端点漂移过大，说明包含了趋势段，
    从起点侧逐步收窄，直到振幅/漂移可接受。
    如果无法满足阈值，退化为最佳收窄（最小振幅且K线数≥90）。
    """
    start = result.structure_start_idx
    end = result.structure_end_idx
    if end - start < 30:
        return

    original_count = result.kline_count
    original_pct = result.range_pct
    need_narrow = False

    # 检查1: 振幅过宽
    if result.range_pct > config.dl_max_range_pct:
        need_narrow = True
        narrow_reason = f"振幅{result.range_pct:.1f}%"

    # 检查2: 端点漂移（前1/4均价 vs 后1/4均价）
    if not need_narrow:
        quarter = max(10, (end - start) // 4)
        q1_avg = df.iloc[start: start + quarter]['Close'].mean()
        q4_avg = df.iloc[end - quarter + 1: end + 1]['Close'].mean()
        mid = (q1_avg + q4_avg) / 2
        drift = abs(q1_avg - q4_avg) / mid * 100 if mid > 0 else 0
        if drift > config.dl_max_drift_pct:
            need_narrow = True
            narrow_reason = f"端点漂移{drift:.1f}%"

    if not need_narrow:
        return

    # 从起点往后逐步推进，每次跳 step 根
    total = end - start + 1
    step = max(1, total // 30)

    # 跟踪最佳收窄位置（最小振幅且K线数≥dl_min_klines）
    best_pct = original_pct
    best_start = start
    best_h = result.range_high
    best_l = result.range_low

    # 记录首个满足range/drift但不足dl_min_klines的位置（兜底用）
    first_valid_narrow = None

    for new_start in range(start + step, end - 30, step):
        seg = df.iloc[new_start: end + 1]
        highs = seg['High'].values
        lows = seg['Low'].values
        h = float(np.percentile(highs, 95))
        l = float(np.percentile(lows, 5))
        m = seg['Close'].mean()
        pct = (h - l) / m * 100 if m > 0 else 0

        # 也检查新起点的漂移
        n_avg = min(5, len(seg) // 4)
        ns_avg = seg.iloc[:n_avg]['Close'].mean()
        ne_avg = seg.iloc[-n_avg:]['Close'].mean()
        nm = (ns_avg + ne_avg) / 2
        d = abs(ns_avg - ne_avg) / nm * 100 if nm > 0 else 0

        # 跟踪最佳位置（K线数仍满足最低要求）
        if pct < best_pct and len(seg) >= config.dl_min_klines:
            best_pct = pct
            best_start = new_start
            best_h = h
            best_l = l

        if pct <= config.dl_max_range_pct and d <= config.dl_max_drift_pct:
            if len(seg) >= config.dl_min_klines:
                # 优先：满足range/drift且K线数充足
                _apply_narrow(result, new_start, end, df, h, l, pct,
                              narrow_reason, original_count)
                return
            elif first_valid_narrow is None:
                # 记录首个满足range/drift的位置（备用）
                first_valid_narrow = (new_start, h, l, pct)

    # 无法满足range/drift + ≥dl_min_klines
    # 尝试退化方案：振幅大幅改善（≥50%）的最佳≥dl_min_klines位置
    if best_start != start and best_pct < original_pct * 0.5:
        _apply_narrow(result, best_start, end, df, best_h, best_l, best_pct,
                      narrow_reason, original_count, fallback=True)
        return

    # 兜底：使用首个满足range/drift的位置（即使K线数不足dl_min_klines）
    if first_valid_narrow:
        ns, fh, fl, fp = first_valid_narrow
        _apply_narrow(result, ns, end, df, fh, fl, fp,
                      narrow_reason, original_count)
        return

    # 原始退化：振幅改善≥30%的最佳≥dl_min_klines位置
    if best_start != start and best_pct < original_pct * 0.7:
        _apply_narrow(result, best_start, end, df, best_h, best_l, best_pct,
                      narrow_reason, original_count, fallback=True)


def _apply_narrow(result: StructureResult, new_start: int, end: int,
                  df: pd.DataFrame, h: float, l: float, pct: float,
                  narrow_reason: str, original_count: int,
                  fallback: bool = False):
    """应用收窄结果到StructureResult"""
    seg = df.iloc[new_start: end + 1]
    result.structure_start_idx = new_start
    result.kline_count = len(seg)
    result.range_high = h
    result.range_low = l
    result.range_pct = round(pct, 2)
    result.passed = result.kline_count >= 90  # dl_min_klines
    result.score = GradeScore.S if result.passed else GradeScore.C
    label = "最佳收窄" if fallback else "收窄"
    result.reasoning.append(
        f"{narrow_reason}过大，{label}: "
        f"{original_count}K→{result.kline_count}K")
