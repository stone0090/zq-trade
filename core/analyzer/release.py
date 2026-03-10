"""
SF 释放级别评估

评估调整结构的尾部是否向突破方向蹭上去了。
好的调整尾部应该保持水平，动能完全蓄积而不是提前释放。

规则（TRADING_RULES.md）：
- SF退化规则：尾部先蹭上去(2nd)但随后≥3根K线回落 → 退化为1st
- SF与PT位关系：偏移必须在接近PT位的区域发生，远离PT位的释放不算偏移
"""
import numpy as np
import pandas as pd

from core.types import (
    AnalyzerConfig, ReleaseResult, ReleaseLevel,
    StructureResult
)


def analyze_release(df: pd.DataFrame,
                    structure: StructureResult,
                    config: AnalyzerConfig = None,
                    direction: str = '',
                    platform=None) -> ReleaseResult:
    """
    SF 释放级别分析。

    评估DL结构是否在后半段向突破方向蹭。
    使用峰值位移法：后半段滚动均线峰值 vs 前半段均价，
    并排除V型结构（前高→中低→尾高）的假释放。
    """
    if config is None:
        config = AnalyzerConfig()

    result = ReleaseResult()
    result.direction = direction

    if structure.kline_count == 0:
        result.reasoning.append("DL未检测到结构，跳过SF分析")
        return result

    start = structure.structure_start_idx
    end = structure.structure_end_idx
    struct_df = df.iloc[start: end + 1]

    if len(struct_df) < 10:
        result.reasoning.append("结构区间数据不足，无法评估释放")
        return result

    close = struct_df['Close'].values
    n = len(close)
    baseline = float(np.median(close))

    # ─── 1. 峰值位移法（后半段滚动均线峰值 vs 前半段均价） ───
    half = n // 2
    window = min(20, max(5, half // 3))
    rolling = pd.Series(close).rolling(window, min_periods=1).mean().values

    first_half_avg = float(np.mean(close[:half]))
    back_rolling = rolling[half:]

    if direction == 'bullish':
        peak_excursion = (float(np.max(back_rolling)) - first_half_avg) / baseline * 100
        peak_excursion = max(0.0, peak_excursion)
    elif direction == 'bearish':
        peak_excursion = (first_half_avg - float(np.min(back_rolling))) / baseline * 100
        peak_excursion = max(0.0, peak_excursion)
    else:
        up = (float(np.max(back_rolling)) - first_half_avg) / baseline * 100
        down = (first_half_avg - float(np.min(back_rolling))) / baseline * 100
        peak_excursion = max(0.0, up, down)

    # ─── 1b. 尾部回落检测：如果后半段曾蹭上去但尾部回落到LK轮廓中游 ───
    tail_k = min(max(4, n // 10), 10)
    tail_close = close[-tail_k:]
    tail_avg = float(np.mean(tail_close))

    if direction == 'bullish':
        final_drift = (tail_avg - first_half_avg) / baseline * 100
        final_drift = max(0.0, final_drift)
    elif direction == 'bearish':
        final_drift = (first_half_avg - tail_avg) / baseline * 100
        final_drift = max(0.0, final_drift)
    else:
        final_drift = max(0.0, abs(tail_avg - first_half_avg) / baseline * 100)

    recovered = False
    degraded = False  # SF退化标记
    original_peak = peak_excursion
    struct_mid = float((struct_df['High'].max() + struct_df['Low'].min()) / 2)

    # 旧有回落检测（峰值→尾部均价回到1st阈值以下）
    recovery_min_peak = config.sf_tail_drift_1st_max * 1.5
    if (peak_excursion > recovery_min_peak
            and peak_excursion <= config.sf_tail_drift_2nd_max
            and final_drift <= config.sf_tail_drift_1st_max):
        if direction == 'bullish':
            at_mid = tail_avg <= struct_mid
        elif direction == 'bearish':
            at_mid = tail_avg >= struct_mid
        else:
            at_mid = abs(tail_avg - struct_mid) / baseline * 100 < 1.0

        tail_range_pct = (float(np.max(tail_close)) - float(np.min(tail_close))) / baseline * 100
        calm_tail = tail_range_pct < 3.5

        if at_mid and calm_tail:
            recovered = True
            peak_excursion = final_drift

    # ─── 1c. SF退化规则：尾部连续回落≥3根 → 从2nd退化为1st ───
    if (not recovered
            and peak_excursion > config.sf_tail_drift_1st_max
            and peak_excursion <= config.sf_tail_drift_2nd_max):
        decline_count = _count_consecutive_declines(close, direction)
        if decline_count >= 3:
            # 验证回落幅度有实际意义
            idx_start = max(0, n - decline_count - 1)
            decline_start_price = float(close[idx_start])
            decline_end_price = float(close[-1])

            if direction == 'bullish':
                retracement_pct = (decline_start_price - decline_end_price) / baseline * 100
            elif direction == 'bearish':
                retracement_pct = (decline_end_price - decline_start_price) / baseline * 100
            else:
                retracement_pct = abs(decline_start_price - decline_end_price) / baseline * 100

            # 回落有效：回落幅度 > 0.3% 或 > 峰值偏移的25%
            min_retracement = max(0.3, peak_excursion * 0.25)
            if retracement_pct >= min_retracement:
                degraded = True
                result.reasoning.append(
                    f"SF退化：尾部{decline_count}根K线连续回落"
                    f"（回落{retracement_pct:.2f}%），从2nd退化为1st"
                )

    # ─── 1d. SF与PT位关系：远离PT位的释放不算偏移 ───
    if (not recovered and not degraded and platform is not None
            and peak_excursion > config.sf_tail_drift_1st_max):
        pt_far = _check_pt_distance(
            struct_df, platform, direction, baseline)
        if pt_far:
            degraded = True
            result.reasoning.append(
                "尾部距PT位较远，整段走势持续释放但不算朝PT方向蹭 → 退化为1st"
            )

    # ─── 2. V型结构检测：前后水平相近，中间低洼 → 回归而非释放 ───
    q = max(n // 4, 5)
    front_q_avg = float(np.mean(close[:q]))
    back_q_avg = float(np.mean(close[-q:]))
    mid_avg = float(np.mean(close[q: n - q])) if n > 2 * q else baseline

    front_back_diff = abs(front_q_avg - back_q_avg) / baseline * 100
    v_depth_front = (front_q_avg - mid_avg) / baseline * 100
    v_depth_back = (back_q_avg - mid_avg) / baseline * 100

    is_v_pattern = (v_depth_front > 0.5 and v_depth_back > 0.5
                    and front_back_diff < 1.0)

    tail_last_close = float(close[-1])
    tail_release_pct = abs(tail_last_close - front_q_avg) / baseline * 100
    tail_has_real_release = tail_release_pct > 1.0

    if not degraded and is_v_pattern and not tail_has_real_release:
        peak_excursion *= 0.25

    drift = round(peak_excursion, 3)
    result.tail_drift_pct = drift
    result.tail_length = n - half

    # ─── 3. 评分 ───
    dir_label = "向上" if direction == 'bullish' else (
        "向下" if direction == 'bearish' else "")

    if degraded:
        # SF退化：强制为1st
        result.score = ReleaseLevel.FIRST
        result.passed = True
        result.reasoning.append(
            f"结构尾部偏移曾达{original_peak:.2f}%，"
            f"但因退化规则生效 → 1st"
        )
        result.action_advice = "尾部回落抵消偏移，条件满足可直接做"

    elif drift <= config.sf_tail_drift_1st_max:
        result.score = ReleaseLevel.FIRST
        result.passed = True
        result.reasoning.append(
            f"结构尾部水平，{dir_label}偏移{drift:.2f}%"
            f"（≤{config.sf_tail_drift_1st_max}%） → 1st"
        )
        result.action_advice = "无明显释放，条件满足可直接做"

    elif drift <= config.sf_tail_drift_2nd_max:
        result.score = ReleaseLevel.SECOND
        result.passed = True
        result.reasoning.append(
            f"尾部{dir_label}蹭了一点，偏移{drift:.2f}%"
            f"（≤{config.sf_tail_drift_2nd_max}%），动能有一定消耗 → 2nd"
        )
        result.action_advice = "动能有一定消耗，需再等一段调整"

    else:
        result.score = ReleaseLevel.THIRD
        result.passed = False
        result.reasoning.append(
            f"尾部{dir_label}蹭幅度很大，偏移{drift:.2f}%"
            f"（>{config.sf_tail_drift_2nd_max}%），动能已消耗完 → 3rd"
        )
        result.action_advice = "动能已消耗完，需等待全新独立结构"

    if is_v_pattern and not tail_has_real_release and not degraded:
        result.reasoning.append("V型结构检测：前后水平相近，中间低洼，峰值已折扣")
    elif is_v_pattern and tail_has_real_release:
        result.reasoning.append(
            f"V型结构检测：检测到V型但尾部有真实释放"
            f"（末K偏离前段{tail_release_pct:.1f}%），不折扣"
        )

    if recovered:
        result.reasoning.append(
            f"尾部回落检测：峰值偏移曾达{original_peak:.2f}%→{drift:.2f}%，"
            f"末端{tail_k}根K线均价{tail_avg:.2f}≤结构中位{struct_mid:.2f}，视为恢复"
        )

    return result


def _count_consecutive_declines(close: np.ndarray, direction: str) -> int:
    """
    从尾部1/4区域的峰值点开始，计算到末端的回落K线数。
    不要求每根K线严格下降，只要整体是从峰值回落到末端即可。
    这更符合人工视觉判断中"尾部N根K线回落"的含义。
    """
    n = len(close)
    if n < 6:
        return 0

    quarter = max(n // 4, 5)
    tail_region = close[-quarter:]

    if direction == 'bullish':
        peak_local_idx = int(np.argmax(tail_region))
        # 峰值在最后2根 → 无实质回落
        if peak_local_idx >= len(tail_region) - 2:
            return 0
        # 峰值后的收盘价必须整体下降（末端 < 峰值）
        if float(close[-1]) >= float(tail_region[peak_local_idx]):
            return 0
        return len(tail_region) - 1 - peak_local_idx
    elif direction == 'bearish':
        trough_local_idx = int(np.argmin(tail_region))
        if trough_local_idx >= len(tail_region) - 2:
            return 0
        if float(close[-1]) <= float(tail_region[trough_local_idx]):
            return 0
        return len(tail_region) - 1 - trough_local_idx
    else:
        return 0


def _check_pt_distance(struct_df: pd.DataFrame, platform,
                       direction: str, baseline: float) -> bool:
    """
    检查尾部是否远离PT位。
    如果尾部均价距PT位超过结构振幅的50%，认为远离PT位。

    Returns:
        True = 远离PT位（释放不算偏移）
    """
    try:
        pt_level = None
        if direction == 'bullish':
            pt_level = platform.resistance_zone_high
        elif direction == 'bearish':
            pt_level = platform.support_zone_low
        else:
            # 方向未知时，取最近的有效PT位
            candidates = []
            if getattr(platform, 'resistance_zone_high', 0) > 0:
                candidates.append(platform.resistance_zone_high)
            if getattr(platform, 'support_zone_low', 0) > 0:
                candidates.append(platform.support_zone_low)
            if not candidates:
                return False
            # 用尾部均价找最近的PT位
            tail_n = max(len(struct_df) // 4, 5)
            tail_avg_tmp = float(struct_df['Close'].iloc[-tail_n:].mean())
            pt_level = min(candidates, key=lambda p: abs(p - tail_avg_tmp))

        if pt_level is None or pt_level <= 0:
            return False
    except AttributeError:
        return False

    struct_range = struct_df['High'].max() - struct_df['Low'].min()
    if struct_range <= 0:
        return False

    # 尾部最后1/4均价
    tail_n = max(len(struct_df) // 4, 5)
    tail_avg = float(struct_df['Close'].iloc[-tail_n:].mean())

    # 距离PT位的百分比（占结构振幅）
    dist = abs(pt_level - tail_avg)
    dist_ratio = dist / struct_range

    # 超过50%的结构振幅 → 认为远离PT位
    return dist_ratio > 0.50
