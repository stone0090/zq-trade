"""
TY 统一区间检测

规则（TRADING_RULES.md）：
1. 排除DN K线：如果末端K线突破PT阻力位，视为DN触发K线，不计入TY
2. 从结构末端（排除DN后）向前连续计数小K线
3. 小K线定义：振幅 < ATR × 0.60
4. "稍大"K线（ATR 60%~120%）可计入TY，但整个TY区间中小K线占比须≥40%
5. 振幅 ≥ ATR × 120% → 序列中断
6. ≤2根 → 待定；3根 → C；≥4根 → B/A/S（由斜率+压缩程度决定）
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

    # ─── 1. ATR 基准 ───
    atr_series = calc_atr(struct_df)
    base_atr = atr_series.mean()
    if base_atr <= 0:
        base_atr = (struct_df['High'] - struct_df['Low']).mean()

    small_threshold = base_atr * config.ty_squeeze_atr_ratio       # < 此值为小K线
    large_threshold = base_atr * config.ty_slightly_large_ratio     # ≥ 此值中断序列

    # ─── 2. 排除DN K线 ───
    ranges = (struct_df['High'] - struct_df['Low']).values
    n = len(ranges)

    dn_skip = _count_dn_bars_at_tail(struct_df, platform)
    effective_tail = n - 1 - dn_skip

    if effective_tail < 2:
        result.pending = True
        result.reasoning.append("结构末端DN K线后剩余K线不足")
        return result

    if dn_skip > 0:
        result.reasoning.append(f"排除末端{dn_skip}根DN K线")

    # ─── 3. 从调整后的末端向前连续计数 ───
    # 当DN已触发（dn_skip>0）时，TY区域在突破K线之前，使用宽松计数
    # 当DN未触发时，TY在结构末端，使用严格计数（sandwich模式）
    relaxed = dn_skip > 0
    seq_result = _count_from_tail(ranges, small_threshold, large_threshold,
                                  effective_tail, relaxed=relaxed)

    if seq_result is None:
        result.pending = True
        result.reasoning.append("末端无小K线，TY=待定")
        return result

    count, seq_start_local, seq_end_local, slightly_large_count = seq_result

    if count <= 2:
        result.pending = True
        result.reasoning.append(f"仅{count}根连续小K线，不足3根，TY=待定")
        return result

    # ─── 有效TY（≥3根），不再是pending ───
    result.pending = False
    result.squeeze_length = count
    result.squeeze_start_idx = start + seq_start_local
    result.squeeze_end_idx = start + seq_end_local
    result.interruptions = slightly_large_count

    # ─── 4. squeeze区统计 ───
    squeeze_df = df.iloc[result.squeeze_start_idx: result.squeeze_end_idx + 1]
    squeeze_ranges = ranges[seq_start_local: seq_end_local + 1]
    avg_range = float(squeeze_ranges.mean())
    avg_range_ratio = avg_range / base_atr if base_atr > 0 else 0

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
            count, slope_pct, avg_range_ratio, config
        )
        result.reasoning.append(
            f"{count}根小K线，斜率{slope_pct:.4f}%，"
            f"压缩度{avg_range_ratio:.1%} → {result.score}"
        )

    result.passed = result.score.value >= GradeScore.B.value

    # 补充信息
    if slightly_large_count > 0:
        result.reasoning.append(
            f"含{slightly_large_count}根稍大K线（ATR 60%~120%）")
    result.reasoning.append(
        f"均幅: {avg_range:.4f}（ATR的{avg_range_ratio:.1%}），基准ATR: {base_atr:.4f}"
    )

    return result


def _count_dn_bars_at_tail(struct_df: pd.DataFrame, platform) -> int:
    """
    检测结构末端的DN触发K线数量。

    DN触发K线：收盘价突破PT阻力区上沿（做多），从末端向前连续检测，
    最多跳过3根。
    """
    if platform is None:
        return 0

    pt_zone_high = platform.resistance_zone_high
    if pt_zone_high <= 0:
        return 0

    n = len(struct_df)
    count = 0
    for i in range(n - 1, max(n - 4, -1), -1):
        close = float(struct_df.iloc[i]['Close'])
        if close > pt_zone_high:
            count = (n - 1) - i + 1
        else:
            break

    return count


def _count_from_tail(ranges: np.ndarray,
                     small_threshold: float,
                     large_threshold: float,
                     tail_end: int = -1,
                     relaxed: bool = False):
    """
    从指定末端向前连续计数小K线。

    strict模式（relaxed=False，DN未触发时）：
    - 最后1根若非小K线可跳过1根；最后2根都不是 → None
    - 允许最多1根"稍大"K线（sandwich：前方须有小K线），计入根数
    - 遇到"明显大"K线（≥ large_threshold）→ 序列中断

    relaxed模式（relaxed=True，DN已触发时）：
    - TY区域在DN突破K线之前，用宽松模式寻找压缩区
    - 连续计数所有 < large_threshold 的K线
    - 计数完成后验证：小K线占比须≥40%，否则无效

    Args:
        ranges: K线振幅数组
        small_threshold: 小K线阈值
        large_threshold: 大K线阈值（序列中断）
        tail_end: 计数起点索引（-1表示使用数组末端）
        relaxed: True=宽松模式（DN已触发），False=严格模式

    Returns:
        (count, start_idx, end_idx, slightly_large_count) 或 None
    """
    n = len(ranges)
    if n == 0:
        return None

    if tail_end < 0:
        tail_end = n - 1

    tail_idx = min(tail_end, n - 1)

    # 确定起点：最多跳过1根大K线
    if ranges[tail_idx] < large_threshold:
        pass
    elif tail_idx >= 1 and ranges[tail_idx - 1] < large_threshold:
        tail_idx = tail_idx - 1
    else:
        return None

    if relaxed:
        return _count_relaxed(ranges, small_threshold, large_threshold, tail_idx)
    else:
        return _count_strict(ranges, small_threshold, large_threshold, tail_idx)


def _count_strict(ranges, small_threshold, large_threshold, tail_idx):
    """严格模式：sandwich容忍，最多1根稍大K线。"""
    count = 0
    slightly_large_used = 0
    max_slightly_large = 1
    seq_end = tail_idx

    i = tail_idx
    while i >= 0:
        r = ranges[i]
        if r < small_threshold:
            count += 1
        elif r < large_threshold:
            if (slightly_large_used < max_slightly_large
                    and i > 0 and ranges[i - 1] < small_threshold):
                slightly_large_used += 1
                count += 1
            else:
                break
        else:
            break
        i -= 1

    seq_start = i + 1
    if count == 0:
        return None
    return (count, seq_start, seq_end, slightly_large_used)


def _count_relaxed(ranges, small_threshold, large_threshold, tail_idx):
    """宽松模式：计数所有非大K线，验证小K线占比≥40%。"""
    count = 0
    small_count = 0
    seq_end = tail_idx

    i = tail_idx
    while i >= 0:
        r = ranges[i]
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
                      config: AnalyzerConfig) -> GradeScore:
    """
    ≥4根时按斜率和压缩程度分级。
    斜率越平、压缩越紧 → 等级越高。
    """
    slope_abs = abs(slope_pct)

    # S: 斜率接近水平 + K线高度压缩
    if (slope_abs < config.ty_slope_s_threshold and
            avg_range_ratio < 0.35):
        return GradeScore.S

    # A: 斜率较平 + 压缩紧密
    if (slope_abs < config.ty_slope_a_threshold and
            avg_range_ratio < 0.50):
        return GradeScore.A

    # 根数多也可以补偿斜率/压缩的不足
    if count >= 6 and slope_abs < config.ty_slope_a_threshold:
        return GradeScore.A

    # B: 基本达标
    return GradeScore.B
