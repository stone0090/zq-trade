"""
TY 统一区间检测

规则（TRADING_RULES.md）：
1. 排除DN K线：末端K线须close>PT上沿 且 振幅>=小K线阈值，小K线不算DN突破
2. 从结构末端（排除DN后）向前连续计数小K线
3. 小K线定义：实体(|Close-Open|) < ATR × 0.55（看实体大小，不含影线）
4. 零振幅K线(High==Low)：尾部跳过，序列中视为中断（数据异常）
5. 实体 ≥ ATR × 0.55 → 序列中断
6. ≤2根 → 待定（图表仍标注）；3根 → C；≥4根 → B/A/S（由斜率+压缩程度决定）
7. 影线不影响TY序列计数，仅作为达到B级后的减分项（通过avg_range_ratio体现）
"""
import numpy as np
import pandas as pd

from core.types import AnalyzerConfig, SqueezeResult, GradeScore, StructureResult
from core.utils.helpers import calc_atr, linear_regression_slope, normalize_slope


def analyze_squeeze(df: pd.DataFrame,
                    structure: StructureResult,
                    config: AnalyzerConfig = None,
                    platform=None) -> SqueezeResult:
    if config is None:
        config = AnalyzerConfig()

    result = SqueezeResult()

    if not structure.passed:
        if structure.structure_start_idx is None or structure.structure_end_idx is None:
            result.reasoning.append("DL未检测到有效结构，跳过TY分析")
            return result

    start = structure.structure_start_idx
    end = structure.structure_end_idx
    struct_df = df.iloc[start: end + 1]

    if len(struct_df) < 10:
        result.reasoning.append("结构区间数据不足，无法检测统一区间")
        return result

    # ─── 1. ATR 基准 + 视觉尺度 ───
    atr_series = calc_atr(struct_df)
    base_atr = atr_series.mean()
    if base_atr <= 0:
        base_atr = (struct_df['High'] - struct_df['Low']).mean()

    atr_small = base_atr * config.ty_squeeze_atr_ratio
    atr_large = base_atr * config.ty_slightly_large_ratio

    # 视觉尺度：当结构ATR显著低于全局ATR时（结构处于压缩区），
    # 用全局ATR作为参考，避免阈值过严导致视觉上明显的小K线被遗漏
    full_atr = calc_atr(df).mean()
    visual_scale_active = base_atr < full_atr * config.ty_visual_atr_gate
    if visual_scale_active:
        visual_small = full_atr * config.ty_squeeze_atr_ratio
        visual_large = full_atr * config.ty_slightly_large_ratio
        small_threshold = max(atr_small, visual_small)
        large_threshold = max(atr_large, visual_large)
    else:
        small_threshold = atr_small
        large_threshold = atr_large

    # ─── 2. 排除DN K线 ───
    # DN bar必须有足够的力度（range >= small_threshold），小K线不可能是DN突破
    ranges = (struct_df['High'] - struct_df['Low']).values
    bodies = np.abs(struct_df['Close'] - struct_df['Open']).values
    opens = struct_df['Open'].values
    closes = struct_df['Close'].values
    n = len(ranges)

    dn_skip = _count_dn_bars_at_tail(struct_df, platform,
                                      min_range=small_threshold)
    effective_tail = n - 1 - dn_skip

    if effective_tail < 2:
        result.pending = True
        result.reasoning.append("结构末端DN K线后剩余K线不足")
        return result

    if dn_skip > 0:
        result.reasoning.append(f"排除末端{dn_skip}根DN K线")

    # ─── 3. 从调整后的末端向前连续计数 ───
    # TY严格模式以实体(body)为准，影线不影响序列判定
    # relaxed模式（DN已触发）仍用range，因为DN前区域需要视觉整体评估
    # 跳空检测：确保TY序列价格连续
    relaxed = dn_skip > 0
    seq_result = _count_from_tail(bodies, ranges, opens, closes,
                                  small_threshold, large_threshold,
                                  effective_tail, relaxed=relaxed)

    if seq_result is None:
        result.pending = True
        result.reasoning.append("末端无小K线，TY=待定")
        return result

    count, seq_start_local, seq_end_local, slightly_large_count = seq_result

    # ─── 记录TY序列信息（无论是否达标都需要） ───
    result.squeeze_length = count
    result.squeeze_start_idx = start + seq_start_local
    result.squeeze_end_idx = start + seq_end_local
    result.interruptions = slightly_large_count

    if count <= 2:
        result.pending = True
        result.reasoning.append(f"仅{count}根连续小K线，不足3根，TY=待定")
        # 仍计算avg_range供图表标注使用
        squeeze_ranges = ranges[seq_start_local: seq_end_local + 1]
        result.avg_range = round(float(squeeze_ranges.mean()), 4)
        return result

    # ─── 有效TY（≥3根），不再是pending ───
    result.pending = False

    # ─── 4. squeeze区统计 ───
    squeeze_df = df.iloc[result.squeeze_start_idx: result.squeeze_end_idx + 1]
    squeeze_ranges = ranges[seq_start_local: seq_end_local + 1]
    avg_range = float(squeeze_ranges.mean())
    # 当视觉尺度生效时，用全局ATR计算压缩比更能反映视觉感受
    grade_base = full_atr if visual_scale_active else base_atr
    avg_range_ratio = avg_range / grade_base if grade_base > 0 else 0

    result.avg_range = round(avg_range, 4)
    result.avg_range_ratio = round(avg_range_ratio, 4)

    # ─── 5. 斜率 ───
    squeeze_close = squeeze_df['Close']
    slope = linear_regression_slope(squeeze_close)
    mean_price = squeeze_close.mean()
    slope_pct = normalize_slope(slope, mean_price)
    result.slope_pct = round(slope_pct, 5)

    # ─── 6. 与DN触发K线间距 ───
    result.gap_to_trigger = dn_skip + (effective_tail - seq_end_local)

    # ─── 7. 评分 ───
    if count == 3:
        # 3根固定为C
        result.score = GradeScore.C
        result.reasoning.append(f"3根小K线，固定 → C")
    elif count >= 4:
        # ≥4根基本达标，由斜率+压缩程度决定B/A/S
        result.score = _grade_by_quality(
            count, slope_pct, avg_range_ratio, config,
            visual_scale=visual_scale_active
        )
        result.reasoning.append(
            f"{count}根小K线，斜率{slope_pct:.4f}%，"
            f"压缩度{avg_range_ratio:.1%} → {result.score}"
        )

    result.passed = result.score.value >= GradeScore.B.value

    # 补充信息
    if slightly_large_count > 0:
        result.reasoning.append(
            f"含{slightly_large_count}根稍大K线（ATR 55%~110%）")
    result.reasoning.append(
        f"均幅: {avg_range:.4f}（ATR的{avg_range_ratio:.1%}），基准ATR: {base_atr:.4f}"
    )
    if visual_scale_active:
        result.reasoning.append(
            f"视觉尺度生效：全局ATR{full_atr:.4f}，"
            f"小K线阈值{small_threshold:.4f}(视觉) > {atr_small:.4f}(ATR)"
        )

    return result


def _count_dn_bars_at_tail(struct_df: pd.DataFrame, platform,
                           min_range: float = 0) -> int:
    """
    检测结构末端的DN触发K线数量。

    DN触发K线：收盘价突破PT阻力区上沿（做多），且K线振幅 >= min_range
    （小K线收盘价微超PT区间不算DN突破）。从末端向前连续检测，最多跳过3根。
    """
    if platform is None:
        return 0

    pt_zone_high = platform.resistance_zone_high
    if pt_zone_high <= 0:
        return 0

    n = len(struct_df)
    count = 0
    for i in range(n - 1, max(n - 4, -1), -1):
        row = struct_df.iloc[i]
        close = float(row['Close'])
        bar_range = float(row['High'] - row['Low'])
        if close > pt_zone_high and bar_range >= min_range:
            count = (n - 1) - i + 1
        else:
            break

    return count


def _count_from_tail(bodies: np.ndarray,
                     ranges: np.ndarray,
                     opens: np.ndarray,
                     closes: np.ndarray,
                     small_threshold: float,
                     large_threshold: float,
                     tail_end: int = -1,
                     relaxed: bool = False):
    """
    从指定末端向前连续计数小K线。

    strict模式（relaxed=False，DN未触发/待定时）：
    - 以实体(body)判断K线大小，影线不影响计数
    - 跳过尾部连续零振幅K线（range==0，数据异常）
    - 遇到实体 ≥ small_threshold、零振幅或跳空 → 序列中断

    relaxed模式（relaxed=True，DN已确认触发时）：
    - 以振幅(range)判断，保留视觉整体评估
    - 连续计数所有 range < large_threshold 的K线
    - 计数完成后验证：小K线占比须≥40%，否则无效

    Returns:
        (count, start_idx, end_idx, slightly_large_count) 或 None
    """
    n = len(bodies)
    if n == 0:
        return None

    if tail_end < 0:
        tail_end = n - 1

    tail_idx = min(tail_end, n - 1)

    # 跳过尾部连续零振幅K线（range==0，数据异常，如非交易时段）
    while tail_idx >= 0 and ranges[tail_idx] == 0:
        tail_idx -= 1
    if tail_idx < 0:
        return None

    if relaxed:
        # relaxed模式：用range判断，跳过1根大K线
        if ranges[tail_idx] < large_threshold:
            pass
        elif tail_idx >= 1 and ranges[tail_idx - 1] < large_threshold:
            tail_idx = tail_idx - 1
        else:
            return None
        return _count_relaxed(ranges, small_threshold, large_threshold, tail_idx)
    else:
        # strict模式：用body判断，跳过1根实体大的K线
        if bodies[tail_idx] < large_threshold:
            pass
        elif tail_idx >= 1 and bodies[tail_idx - 1] < large_threshold:
            tail_idx = tail_idx - 1
        else:
            return None
        return _count_strict(bodies, ranges, opens, closes,
                             small_threshold, tail_idx)


def _count_strict(bodies, ranges, opens, closes, small_threshold, tail_idx):
    """
    严格模式：从tail_idx向前连续计数实体小的K线。

    以实体(|Close-Open|)判断K线大小，影线不影响计数。
    零振幅K线(range==0)视为数据异常中断；十字星(body==0, range>0)视为小K线。
    跳空检测：若相邻K线间价格跳空 > small_threshold，视为不连续中断。
    """
    count = 0
    seq_end = tail_idx
    seq_start = tail_idx

    i = tail_idx
    while i >= 0:
        if ranges[i] == 0:  # 数据异常（无交易），中断
            break
        if bodies[i] >= small_threshold:  # 实体过大，中断
            break
        # 跳空检测：当前K线与后一根（已计入序列）之间有价格跳空
        if count > 0:
            gap = abs(opens[i + 1] - closes[i])
            if gap > small_threshold:
                break
        count += 1
        seq_start = i
        i -= 1

    if count == 0:
        return None
    return (count, seq_start, seq_end, 0)


def _count_relaxed(ranges, small_threshold, large_threshold, tail_idx):
    """宽松模式：用振幅(range)计数所有非大K线，验证小K线占比≥40%。"""
    count = 0
    small_count = 0
    seq_end = tail_idx

    i = tail_idx
    while i >= 0:
        r = ranges[i]
        if r == 0:  # 数据异常，中断
            break
        if r < large_threshold:
            count += 1
            if r < small_threshold:
                small_count += 1
        else:
            break
        i -= 1

    seq_start = i + 1
    if count == 0:
        return None

    # 小K线占比须≥40%
    if small_count / count < 0.40:
        return None

    slightly_large_count = count - small_count
    return (count, seq_start, seq_end, slightly_large_count)


def _grade_by_quality(count: int, slope_pct: float,
                      avg_range_ratio: float,
                      config: AnalyzerConfig,
                      visual_scale: bool = False) -> GradeScore:
    """
    ≥4根时按斜率和压缩程度分级。
    斜率越平、压缩越紧 → 等级越高。
    当视觉尺度生效时（结构处于压缩区），适当放宽斜率要求。
    """
    slope_abs = abs(slope_pct)

    # 视觉尺度下放宽斜率阈值：压缩区内的轻微漂移在全局视角下不显著
    slope_a = config.ty_slope_a_threshold * (2.0 if visual_scale else 1.0)
    slope_s = config.ty_slope_s_threshold

    # S: 斜率接近水平 + K线高度压缩
    if (slope_abs < slope_s and
            avg_range_ratio < 0.35):
        return GradeScore.S

    # 近零斜率 + 压缩度尚可 → A（极平斜率补偿略高的平均振幅，
    # 典型场景：TY序列含1根稍大K线拉高了均值，但斜率几乎为零）
    if (slope_abs < slope_s and
            avg_range_ratio <= 0.65):
        return GradeScore.A

    # A: 斜率较平 + 压缩紧密
    if (slope_abs < slope_a and
            avg_range_ratio <= 0.55):
        return GradeScore.A

    # 根数多也可以补偿斜率/压缩的不足
    if count >= 6 and slope_abs < slope_a:
        return GradeScore.A

    # B: 基本达标
    return GradeScore.B
