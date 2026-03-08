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

    评估DL结构的尾部是否向突破方向蹭。
    做多时看尾部是否向上蹭，做空时看尾部是否向下蹭。
    1st=水平无蹭 / 2nd=蹭了一点 / 3rd=蹭了很多。
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

    # ─── 1. 多尺度尾部偏移检测 ───
    # 检查 last 1/4, 1/3, 1/2，取方向性最大偏移
    tail_fractions = [0.25, 0.33, 0.50]
    max_directional_drift = 0.0
    best_tail_len = 0

    for frac in tail_fractions:
        tail_len = max(int(n * frac), 5)
        if tail_len >= n:
            continue
        tail_avg = float(np.mean(close[-tail_len:]))
        raw_drift = (tail_avg - baseline) / baseline * 100

        # 方向性偏移：只关心向突破方向蹭的幅度
        if direction == 'bullish':
            directional_drift = max(0.0, raw_drift)  # 向上蹭才算
        elif direction == 'bearish':
            directional_drift = max(0.0, -raw_drift)  # 向下蹭才算
        else:
            directional_drift = abs(raw_drift)  # 方向未定，用绝对值

        if directional_drift > max_directional_drift:
            max_directional_drift = directional_drift
            best_tail_len = tail_len

    result.tail_drift_pct = round(max_directional_drift, 3)
    result.tail_length = best_tail_len

    # ─── 2. 评分 ───
    drift = max_directional_drift
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

    return result
