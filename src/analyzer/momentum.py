"""
DN 动能分析

评估突破K线相对于统一区间的压倒性力量，
验证是否突破关键阻力位、成交量配合等。
"""
import numpy as np
import pandas as pd

from src.analyzer.base import (
    AnalyzerConfig, MomentumResult, GradeScore,
    StructureResult, PlatformResult, SqueezeResult
)
from src.utils.helpers import candle_body_size


def analyze_momentum(df: pd.DataFrame,
                     structure: StructureResult,
                     platform: PlatformResult,
                     squeeze: SqueezeResult,
                     config: AnalyzerConfig = None) -> MomentumResult:
    """
    DN 动能分析。

    识别突破K线，评估其力度、方向、是否突破平台位、成交量配合。
    """
    if config is None:
        config = AnalyzerConfig()

    result = MomentumResult()

    if not structure.passed:
        result.reasoning.append("DL未通过，跳过DN分析")
        return result

    end = structure.structure_end_idx
    range_high = structure.range_high
    range_low = structure.range_low

    # ─── 1. 确定突破方向 ───
    # 从结构末端开始向后扫描（不往前回看，避免将盘整内波动误判为突破）
    # 扫描范围: 结构末端到之后10根K线
    scan_start = end
    scan_end = min(len(df) - 1, end + 10)

    # 计算结构区间的ATR作为力度基准（当TY未检测到时的备选基准）
    struct_df = df.iloc[structure.structure_start_idx: end + 1]
    struct_atr = (struct_df['High'] - struct_df['Low']).mean()

    trigger_idx = -1
    direction = ''
    merged_count = 1

    # 向上突破检测: 收盘价实质性突破上沿
    for i in range(scan_start, scan_end + 1):
        row = df.iloc[i]
        body = abs(row['Close'] - row['Open'])
        if row['Close'] > range_high and body > struct_atr * 0.3:
            trigger_idx = i
            direction = 'bullish'
            break

    # 如果单根未突破，尝试合并
    if trigger_idx == -1:
        merged_result = _try_merged_breakout(df, scan_start, scan_end,
                                             range_high, range_low, config.dn_max_merged)
        if merged_result:
            trigger_idx, direction, merged_count = merged_result

    # 向下突破检测（如果向上没有）
    if trigger_idx == -1:
        for i in range(scan_start, scan_end + 1):
            row = df.iloc[i]
            body = abs(row['Close'] - row['Open'])
            if row['Close'] < range_low and body > struct_atr * 0.3:
                trigger_idx = i
                direction = 'bearish'
                break

    # ─── 2. 如果仍无突破 → PENDING ───
    if trigger_idx == -1:
        result.pending = True
        result.reasoning.append("尚未出现突破K线，等待触发信号")
        return result

    result.trigger_idx = trigger_idx
    result.direction = direction
    result.merged_count = merged_count

    trigger_row = df.iloc[trigger_idx]
    result.trigger_close = float(trigger_row['Close'])

    # ─── 3. 力度计算 ───
    trigger_body = candle_body_size(trigger_row)
    # 如果是合并K线
    if merged_count > 1:
        merge_start = trigger_idx - merged_count + 1
        trigger_body = abs(df.iloc[trigger_idx]['Close'] - df.iloc[merge_start]['Open'])

    result.trigger_range = round(trigger_body, 4)

    # 与 squeeze 区平均振幅对比（若TY未检测到，用结构ATR替代）
    if squeeze.avg_range > 0:
        squeeze_avg = squeeze.avg_range
    else:
        squeeze_avg = struct_atr if struct_atr > 0 else 0.01
    force_ratio = trigger_body / squeeze_avg
    result.force_ratio = round(force_ratio, 2)

    # ─── 4. 平台位突破确认 ───
    if platform.passed and platform.platform_price > 0:
        # 根据突破方向选择对应平台区间边界
        if direction == 'bullish' and platform.resistance_price > 0:
            # 向上突破需突破阻力区间上沿
            pp = platform.resistance_zone_high if platform.resistance_zone_high > 0 else platform.resistance_price
        elif direction == 'bearish' and platform.support_price > 0:
            # 向下突破需跌破支撑区间下沿
            pp = platform.support_zone_low if platform.support_zone_low > 0 else platform.support_price
        else:
            pp = platform.platform_price

        if direction == 'bullish':
            result.broke_platform = trigger_row['Close'] > pp
        else:
            result.broke_platform = trigger_row['Close'] < pp
    else:
        result.broke_platform = False

    # ─── 5. 成交量对比 ───
    if squeeze.squeeze_start_idx < squeeze.squeeze_end_idx:
        squeeze_df = df.iloc[squeeze.squeeze_start_idx: squeeze.squeeze_end_idx + 1]
        avg_vol = squeeze_df['Volume'].mean()
        if avg_vol > 0:
            if merged_count > 1:
                merge_start = trigger_idx - merged_count + 1
                trigger_vol = df.iloc[merge_start: trigger_idx + 1]['Volume'].sum()
            else:
                trigger_vol = trigger_row['Volume']
            result.volume_ratio = round(trigger_vol / avg_vol, 2)

    # ─── 6. 评分 ───
    fr = result.force_ratio
    mc = result.merged_count
    bp = result.broke_platform
    vr = result.volume_ratio

    if (mc == 1 and fr >= config.dn_force_ratio_s and
            bp and vr >= config.dn_volume_ratio_s):
        result.score = GradeScore.S
        result.reasoning.append(
            f"单根突破，力度{fr:.1f}x，突破平台位，放量{vr:.1f}x → S"
        )
    elif mc == 1 and fr >= config.dn_force_ratio_a and bp:
        result.score = GradeScore.A
        result.reasoning.append(
            f"单根突破，力度{fr:.1f}x，突破平台位 → A"
        )
    elif (mc <= 2 and fr >= config.dn_force_ratio_b) or (mc == 1 and fr >= config.dn_force_ratio_b):
        result.score = GradeScore.B
        result.reasoning.append(
            f"{'单根' if mc==1 else f'{mc}根合并'}突破，力度{fr:.1f}x → B"
        )
    else:
        result.score = GradeScore.C
        reasons = []
        if mc > 2:
            reasons.append(f"{mc}根合并动能偏弱")
        if fr < config.dn_force_ratio_b:
            reasons.append(f"力度仅{fr:.1f}x")
        if not bp:
            reasons.append("未突破平台位")
        result.reasoning.append("、".join(reasons) + " → C")

    result.passed = result.score.value >= GradeScore.B.value

    # 补充信息
    dir_label = "向上" if direction == 'bullish' else "向下"
    result.reasoning.append(
        f"{dir_label}突破，触发价: {result.trigger_close:.3f}，"
        f"结构上沿: {range_high:.3f}，下沿: {range_low:.3f}"
    )

    return result


def _try_merged_breakout(df: pd.DataFrame, scan_start: int, scan_end: int,
                         range_high: float, range_low: float,
                         max_merged: int) -> tuple:
    """
    尝试合并连续K线判定突破。

    Returns:
        (trigger_idx, direction, merged_count) 或 None
    """
    # 向上合并
    for merge_n in range(2, max_merged + 1):
        for i in range(scan_start, scan_end - merge_n + 2):
            merge_end = i + merge_n - 1
            if merge_end > scan_end:
                break
            merged_close = df.iloc[merge_end]['Close']
            merged_open = df.iloc[i]['Open']
            # 合并后整体向上
            if merged_close > merged_open and merged_close > range_high:
                return (merge_end, 'bullish', merge_n)

    # 向下合并
    for merge_n in range(2, max_merged + 1):
        for i in range(scan_start, scan_end - merge_n + 2):
            merge_end = i + merge_n - 1
            if merge_end > scan_end:
                break
            merged_close = df.iloc[merge_end]['Close']
            merged_open = df.iloc[i]['Open']
            if merged_close < merged_open and merged_close < range_low:
                return (merge_end, 'bearish', merge_n)

    return None
