"""
TY 统一区间检测

在盘整结构尾部，寻找K线极小、几乎水平的压缩区——蓄势待发的最后阶段。
"""
import numpy as np
import pandas as pd

from src.analyzer.base import AnalyzerConfig, SqueezeResult, GradeScore, StructureResult
from src.utils.helpers import calc_atr, linear_regression_slope, normalize_slope


def analyze_squeeze(df: pd.DataFrame,
                    structure: StructureResult,
                    config: AnalyzerConfig = None) -> SqueezeResult:
    """
    TY 统一区间分析。

    从结构尾部扫描，找到连续的小K线压缩区，验证斜率和长度。
    """
    if config is None:
        config = AnalyzerConfig()

    result = SqueezeResult()

    if not structure.passed:
        result.reasoning.append("DL未通过，跳过TY分析")
        return result

    start = structure.structure_start_idx
    end = structure.structure_end_idx
    struct_df = df.iloc[start: end + 1]

    if len(struct_df) < 10:
        result.reasoning.append("结构区间数据不足，无法检测统一区间")
        return result

    # ─── 1. ATR 基准 ───
    atr_series = calc_atr(struct_df)
    base_atr = atr_series.mean()
    if base_atr <= 0:
        base_atr = (struct_df['High'] - struct_df['Low']).mean()

    squeeze_threshold = base_atr * config.ty_squeeze_atr_ratio

    # ─── 2. 标记小K线 ───
    scan_len = min(config.ty_scan_window, len(struct_df))
    tail_df = struct_df.iloc[-scan_len:]
    ranges = (tail_df['High'] - tail_df['Low']).values
    is_small = ranges < squeeze_threshold

    # ─── 3. 从尾部往前扫描最长连续小K线序列 ───
    best_seq = _find_best_squeeze_sequence(is_small, config.ty_max_interruptions)

    if best_seq is None:
        result.reasoning.append("未检测到有效统一区间（无连续小K线序列）")
        return result

    seq_start_local, seq_end_local, interruptions = best_seq
    squeeze_length = seq_end_local - seq_start_local + 1

    # 转换为在完整df中的索引
    tail_start_in_df = len(struct_df) - scan_len
    squeeze_start_in_struct = tail_start_in_df + seq_start_local
    squeeze_end_in_struct = tail_start_in_df + seq_end_local

    squeeze_start_abs = start + squeeze_start_in_struct
    squeeze_end_abs = start + squeeze_end_in_struct

    result.squeeze_length = squeeze_length
    result.squeeze_start_idx = squeeze_start_abs
    result.squeeze_end_idx = squeeze_end_abs
    result.interruptions = interruptions

    # ─── 4. squeeze区均值统计 ───
    squeeze_df = df.iloc[squeeze_start_abs: squeeze_end_abs + 1]
    avg_range = float(ranges[seq_start_local: seq_end_local + 1].mean())
    avg_range_ratio = avg_range / base_atr if base_atr > 0 else 0

    result.avg_range = round(avg_range, 4)
    result.avg_range_ratio = round(avg_range_ratio, 4)

    # ─── 5. 斜率检验 ───
    squeeze_close = squeeze_df['Close']
    slope = linear_regression_slope(squeeze_close)
    mean_price = squeeze_close.mean()
    slope_pct = normalize_slope(slope, mean_price)
    result.slope_pct = round(slope_pct, 5)

    # ─── 6. 与结构末端(触发K线位置)的间距 ───
    gap = end - squeeze_end_abs
    result.gap_to_trigger = gap

    # ─── 7. 评分 ───
    if (squeeze_length >= 6 and
            slope_pct < config.ty_slope_s_threshold and
            gap <= config.ty_max_gap_to_trigger and
            interruptions == 0):
        result.score = GradeScore.S
        result.reasoning.append(
            f"连续{squeeze_length}根小K线，斜率{slope_pct:.4f}%，"
            f"无夹杂，间距{gap}根 → S"
        )
    elif (squeeze_length >= 4 and
          slope_pct < config.ty_slope_a_threshold and
          gap <= config.ty_max_gap_to_trigger):
        result.score = GradeScore.A
        result.reasoning.append(
            f"连续{squeeze_length}根小K线，斜率{slope_pct:.4f}%，"
            f"间距{gap}根 → A"
        )
    elif (squeeze_length >= 3 and
          slope_pct < config.ty_slope_b_threshold):
        result.score = GradeScore.B
        result.reasoning.append(
            f"{squeeze_length}根小K线，斜率{slope_pct:.4f}% → B"
        )
    else:
        result.score = GradeScore.C
        reasons = []
        if squeeze_length < 3:
            reasons.append(f"仅{squeeze_length}根小K线")
        if slope_pct >= config.ty_slope_b_threshold:
            reasons.append(f"斜率{slope_pct:.4f}%过大")
        if gap > config.ty_max_gap_to_trigger:
            reasons.append(f"间距{gap}根过远")
        result.reasoning.append("、".join(reasons) + " → C")

    result.passed = result.score.value >= GradeScore.B.value

    # 补充信息
    result.reasoning.append(
        f"均幅: {avg_range:.4f}（ATR的{avg_range_ratio:.1%}），"
        f"基准ATR: {base_atr:.4f}"
    )

    return result


def _find_best_squeeze_sequence(is_small: np.ndarray,
                                max_interruptions: int) -> tuple:
    """
    从尾部往前扫描，找到允许少量中断的最长连续小K线序列。

    Returns:
        (start_idx, end_idx, interruption_count) 或 None
    """
    n = len(is_small)
    if n == 0:
        return None

    best = None
    best_length = 0

    # 从尾部开始扫描
    i = n - 1
    while i >= 0:
        if not is_small[i]:
            i -= 1
            continue

        # 找到一个小K线，向前扩展
        seq_end = i
        seq_start = i
        interruptions = 0
        j = i - 1

        while j >= 0:
            if is_small[j]:
                seq_start = j
            else:
                interruptions += 1
                if interruptions > max_interruptions:
                    break
                # 检查前面是否还有小K线（避免中断后无续接）
                if j > 0 and is_small[j - 1]:
                    seq_start = j  # 暂时包含这个中断
                else:
                    break
            j -= 1

        length = seq_end - seq_start + 1
        # 实际小K线数 = length - interruptions
        effective = length - interruptions

        if effective > best_length and effective >= 2:
            best_length = effective
            best = (seq_start, seq_end, interruptions)

        # 从 seq_start 之前继续扫描
        i = seq_start - 1

    return best
