"""
SF 释放级别评估

评估在触发K线之前（结构形成之后），价格是否已经有了方向性运动，
以此判断风险收益比。
"""
import numpy as np
import pandas as pd

from src.analyzer.base import (
    AnalyzerConfig, ReleaseResult, ReleaseLevel,
    StructureResult, MomentumResult
)


def analyze_release(df: pd.DataFrame,
                    structure: StructureResult,
                    momentum: MomentumResult,
                    config: AnalyzerConfig = None) -> ReleaseResult:
    """
    SF 释放级别分析。

    观察结构结束到触发K线之间的价格运动幅度。
    """
    if config is None:
        config = AnalyzerConfig()

    result = ReleaseResult()

    if not structure.passed:
        result.reasoning.append("DL未通过，跳过SF分析")
        return result

    if momentum.pending:
        # DN尚未触发，但仍需检测结构末端到当前是否已有释放
        # SF不依赖DN，独立评估结构本身的释放情况
        struct_end = structure.structure_end_idx
        last_idx = len(df) - 1
        range_high = structure.range_high
        range_low = structure.range_low

        if last_idx > struct_end and range_high > 0 and range_low > 0:
            window_df = df.iloc[struct_end:last_idx + 1]
            release_bars = last_idx - struct_end
            result.release_bars = release_bars

            # 检测向上释放（价格突破结构上沿）
            highest = window_df['High'].max()
            up_pct = max(0, (highest - range_high) / range_high * 100)
            # 检测向下释放（价格跌破结构下沿）
            lowest = window_df['Low'].min()
            down_pct = max(0, (range_low - lowest) / range_low * 100)
            release_pct = max(up_pct, down_pct)
            result.release_pct = round(release_pct, 3)
            result.release_speed = round(release_pct / release_bars, 4) if release_bars > 0 else 0

            if (release_pct >= config.sf_second_max_pct or
                    release_bars > config.sf_second_max_bars):
                result.score = ReleaseLevel.THIRD
                result.passed = False
                result.reasoning.append(
                    f"DN待定，但已释放{release_pct:.2f}%/{release_bars}根 → 3rd"
                )
                result.action_advice = "释放过大，需等待全新独立结构"
            elif (release_pct >= config.sf_first_max_pct or
                  release_bars > config.sf_first_max_bars):
                result.score = ReleaseLevel.SECOND
                result.passed = True
                result.reasoning.append(
                    f"DN待定，已有释放{release_pct:.2f}%/{release_bars}根 → 2nd"
                )
                result.action_advice = "已有前置释放，需等回踩后执行"
            else:
                result.score = ReleaseLevel.FIRST
                result.passed = True
                result.reasoning.append("DN待定，无明显前置释放 → 1st")
                result.action_advice = "等待触发信号"
        else:
            result.score = ReleaseLevel.FIRST
            result.passed = True
            result.reasoning.append("尚未出现突破，默认按1st评估（无前置释放）")
            result.action_advice = "等待触发信号"

        return result

    struct_end = structure.structure_end_idx
    trigger_idx = momentum.trigger_idx
    direction = momentum.direction
    range_high = structure.range_high
    range_low = structure.range_low

    # ─── 1. 观察区间 ───
    if trigger_idx <= struct_end:
        # 触发K线在结构内部 — 但仍需检查触发K线本身是否已超出结构范围
        trigger_bar = df.iloc[trigger_idx]
        if direction == 'bullish':
            release_pct = max(0, (trigger_bar['High'] - range_high) / range_high * 100)
        else:
            release_pct = max(0, (range_low - trigger_bar['Low']) / range_low * 100)

        if release_pct >= config.sf_second_max_pct:
            result.score = ReleaseLevel.THIRD
            result.passed = False
            result.release_pct = round(release_pct, 3)
            result.release_bars = 0
            result.reasoning.append(
                f"触发K线已释放{release_pct:.2f}%（≥{config.sf_second_max_pct}%） → 3rd"
            )
            result.action_advice = "释放过大，需等待全新独立结构"
            return result
        elif release_pct >= config.sf_first_max_pct:
            result.score = ReleaseLevel.SECOND
            result.passed = True
            result.release_pct = round(release_pct, 3)
            result.release_bars = 0
            result.reasoning.append(
                f"触发K线已释放{release_pct:.2f}%（≥{config.sf_first_max_pct}%） → 2nd"
            )
            result.action_advice = "已有释放，需等回踩后执行"
            return result
        else:
            result.score = ReleaseLevel.FIRST
            result.passed = True
            result.release_pct = round(release_pct, 3)
            result.release_bars = 0
            result.reasoning.append("触发K线在结构内，释放有限 → 1st")
            result.action_advice = "条件满足，可直接执行"
            return result

    release_bars = trigger_idx - struct_end
    result.release_bars = release_bars

    if release_bars <= 0:
        result.score = ReleaseLevel.FIRST
        result.passed = True
        result.reasoning.append("无前置释放区间 → 1st")
        result.action_advice = "条件满足，可直接执行"
        return result

    # ─── 2. 释放幅度计算 ───
    window_df = df.iloc[struct_end: trigger_idx + 1]

    if direction == 'bullish':
        # 向上突破前的向上释放
        highest = window_df['High'].max()
        release_pct = (highest - range_high) / range_high * 100 if range_high > 0 else 0
    else:
        # 向下突破前的向下释放
        lowest = window_df['Low'].min()
        release_pct = (range_low - lowest) / range_low * 100 if range_low > 0 else 0

    release_pct = max(0, release_pct)  # 不能为负
    result.release_pct = round(release_pct, 3)

    # 释放速度
    release_speed = release_pct / release_bars if release_bars > 0 else 0
    result.release_speed = round(release_speed, 4)

    # ─── 3. 评分 ───
    if (release_pct < config.sf_first_max_pct and
            release_bars <= config.sf_first_max_bars):
        result.score = ReleaseLevel.FIRST
        result.passed = True
        result.reasoning.append(
            f"释放{release_pct:.2f}%（<{config.sf_first_max_pct}%），"
            f"{release_bars}根K线（≤{config.sf_first_max_bars}） → 1st"
        )
        result.action_advice = "条件满足，可直接执行"

    elif (release_pct < config.sf_second_max_pct and
          release_bars <= config.sf_second_max_bars):
        result.score = ReleaseLevel.SECOND
        result.passed = True
        result.reasoning.append(
            f"释放{release_pct:.2f}%，{release_bars}根K线 → 2nd"
        )
        result.action_advice = "需等待回踩平台位后再执行"

    else:
        result.score = ReleaseLevel.THIRD
        result.passed = False
        result.reasoning.append(
            f"释放{release_pct:.2f}%，{release_bars}根K线，释放过大 → 3rd"
        )
        result.action_advice = "释放过大，需等待全新独立结构"

    return result
