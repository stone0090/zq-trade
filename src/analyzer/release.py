"""
SF 释放级别评估

评估调整结构的尾部是否向突破方向蹭上去了。
好的调整尾部应该保持水平，动能完全蓄积而不是提前释放。
"""
import numpy as np
import pandas as pd

from src.analyzer.base import (
    AnalyzerConfig, ReleaseResult, ReleaseLevel,
    StructureResult
)


def analyze_release(df: pd.DataFrame,
                    structure: StructureResult,
                    config: AnalyzerConfig = None,
                    direction: str = '') -> ReleaseResult:
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

    # ─── 2. V型结构检测：前后水平相近，中间低洼 → 回归而非释放 ───
    q = max(n // 4, 5)
    front_q_avg = float(np.mean(close[:q]))
    back_q_avg = float(np.mean(close[-q:]))
    mid_avg = float(np.mean(close[q: n - q])) if n > 2 * q else baseline

    front_back_diff = abs(front_q_avg - back_q_avg) / baseline * 100
    v_depth_front = (front_q_avg - mid_avg) / baseline * 100
    v_depth_back = (back_q_avg - mid_avg) / baseline * 100

    # V型：前后都高于中间，且前后接近
    is_v_pattern = (v_depth_front > 0.5 and v_depth_back > 0.5
                    and front_back_diff < 1.0)

    if is_v_pattern:
        peak_excursion *= 0.25  # V型结构大幅折扣，回归不算释放

    drift = round(peak_excursion, 3)
    result.tail_drift_pct = drift
    result.tail_length = n - half  # 后半段长度

    # ─── 3. 评分 ───
    dir_label = "向上" if direction == 'bullish' else (
        "向下" if direction == 'bearish' else "")

    if drift <= config.sf_tail_drift_1st_max:
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

    if is_v_pattern:
        result.reasoning.append("V型结构检测：前后水平相近，中间低洼，峰值已折扣")

    return result
