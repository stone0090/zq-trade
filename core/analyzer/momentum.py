"""
DN 动能分析

评估突破K线相对于统一区间的压倒性力量，
验证是否突破关键阻力位、成交量配合等。
补充：检查是否超越PT瑕疵高点和前期小高点。
"""
import numpy as np
import pandas as pd

from core.types import (
    AnalyzerConfig, MomentumResult, GradeScore,
    StructureResult, PlatformResult, SqueezeResult
)
from core.utils.helpers import candle_body_size


def analyze_momentum(df: pd.DataFrame,
                     structure: StructureResult,
                     platform: PlatformResult,
                     squeeze: SqueezeResult,
                     config: AnalyzerConfig = None) -> MomentumResult:
    """
    DN 动能分析。

    识别突破K线，评估其力度、方向、是否突破平台位、成交量配合。
    额外检查PT瑕疵高点和结构内小高点是否被突破。
    """
    if config is None:
        config = AnalyzerConfig()

    result = MomentumResult()

    if structure.kline_count == 0:
        result.reasoning.append("DL未检测到结构，跳过DN分析")
        return result

    end = structure.structure_end_idx
    range_high = structure.range_high
    range_low = structure.range_low

    # ─── 1. 确定突破方向 ───
    # TY之后应立即出现DN：有TY时从TY末端开始扫描
    if squeeze.squeeze_length > 0 and squeeze.squeeze_end_idx > 0:
        scan_start = squeeze.squeeze_end_idx
        # TY后窗口收紧：只看 gap + 合并K线数 的范围
        scan_end = min(len(df) - 1,
                       scan_start + config.ty_max_gap_to_trigger + config.dn_max_merged)
    else:
        scan_start = end
        scan_end = min(len(df) - 1, end + 10)

    struct_df = df.iloc[structure.structure_start_idx: end + 1]
    struct_atr = (struct_df['High'] - struct_df['Low']).mean()

    # 综合评估: 同时检测单根 + 合并(最多3根)，选最强突破
    trigger_idx, direction, merged_count = _find_best_breakout(
        df, scan_start, scan_end, range_high, range_low,
        struct_atr, config.dn_max_merged)

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
    if merged_count > 1:
        merge_start = trigger_idx - merged_count + 1
        trigger_body = abs(df.iloc[trigger_idx]['Close'] - df.iloc[merge_start]['Open'])

    result.trigger_range = round(trigger_body, 4)

    if squeeze.avg_range > 0:
        squeeze_avg = squeeze.avg_range
    else:
        squeeze_avg = struct_atr if struct_atr > 0 else 0.01
    force_ratio = trigger_body / squeeze_avg
    result.force_ratio = round(force_ratio, 2)

    # ─── 4. 平台位突破确认 ───
    # 直接检查对应方向的阻力/支撑价格（activate_platform在DN之后调用，
    # 此时platform.passed尚未设置，不能依赖它）
    if direction == 'bullish' and platform.resistance_price > 0:
        pp = platform.resistance_zone_high if platform.resistance_zone_high > 0 else platform.resistance_price
        result.broke_platform = trigger_row['Close'] > pp
    elif direction == 'bearish' and platform.support_price > 0:
        pp = platform.support_zone_low if platform.support_zone_low > 0 else platform.support_price
        result.broke_platform = trigger_row['Close'] < pp
    else:
        result.broke_platform = False

    # ─── 5. PT瑕疵高点和前期小高点检查 ───
    flaw_exceeded = _check_flaw_high_exceeded(
        df, structure, platform, direction, trigger_row['Close']
    )

    # ─── 6. 成交量对比 ───
    avg_vol = 0
    if squeeze.squeeze_start_idx < squeeze.squeeze_end_idx:
        squeeze_vol_df = df.iloc[squeeze.squeeze_start_idx: squeeze.squeeze_end_idx + 1]
        avg_vol = squeeze_vol_df['Volume'].mean()
    else:
        # 无TY时，用结构尾部20根K线的均量作为基准
        tail_n = min(20, len(struct_df))
        tail_vol_df = struct_df.iloc[-tail_n:]
        avg_vol = tail_vol_df['Volume'].mean()

    if avg_vol > 0:
        if merged_count > 1:
            merge_start = trigger_idx - merged_count + 1
            trigger_vol = df.iloc[merge_start: trigger_idx + 1]['Volume'].sum()
        else:
            trigger_vol = trigger_row['Volume']
        result.volume_ratio = round(trigger_vol / avg_vol, 2)

    # ─── 7. 评分 ───
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
        reasons_not_s = []
        if fr < config.dn_force_ratio_s:
            reasons_not_s.append(f"力度{fr:.1f}x未达S({config.dn_force_ratio_s:.1f}x)")
        if vr < config.dn_volume_ratio_s:
            reasons_not_s.append(f"放量{vr:.1f}x不足S({config.dn_volume_ratio_s:.1f}x)")
        reason_str = "，".join(reasons_not_s) if reasons_not_s else "综合略差于S"
        result.reasoning.append(
            f"单根突破，力度{fr:.1f}x，突破平台位 → A（{reason_str}）"
        )
    elif (mc <= 2 and fr >= config.dn_force_ratio_b) or (mc == 1 and fr >= config.dn_force_ratio_b):
        result.score = GradeScore.B
        reasons_not_a = []
        if mc > 1:
            reasons_not_a.append(f"{mc}根合并非单根")
        if fr < config.dn_force_ratio_a:
            reasons_not_a.append(f"力度{fr:.1f}x未达A({config.dn_force_ratio_a:.1f}x)")
        if not bp:
            reasons_not_a.append("未突破平台位")
        reason_str = "，".join(reasons_not_a) if reasons_not_a else "综合未达A"
        result.reasoning.append(
            f"{'单根' if mc==1 else f'{mc}根合并'}突破，力度{fr:.1f}x → B（{reason_str}）"
        )
    else:
        # 力度不足，视为尚未有效突破 → pending
        result.pending = True
        reasons = []
        if mc > 2:
            reasons.append(f"{mc}根合并动能偏弱")
        if fr < config.dn_force_ratio_b:
            reasons.append(f"力度仅{fr:.1f}x")
        if not bp:
            reasons.append("未突破平台位")
        result.reasoning.append("、".join(reasons) + " → 力度不足，视为待定")

    # 瑕疵高点检查结果补充到reasoning
    if flaw_exceeded is not None:
        if flaw_exceeded:
            result.reasoning.append("已超越PT瑕疵高点/前期小高点")
        else:
            result.reasoning.append("警告: 未超越PT瑕疵高点/前期小高点")

    result.passed = result.score.value >= GradeScore.B.value

    # 补充信息
    dir_label = "向上" if direction == 'bullish' else "向下"
    result.reasoning.append(
        f"{dir_label}突破，触发价: {result.trigger_close:.3f}，"
        f"结构上沿: {range_high:.3f}，下沿: {range_low:.3f}"
    )

    return result


def _check_flaw_high_exceeded(df: pd.DataFrame,
                              structure: StructureResult,
                              platform: PlatformResult,
                              direction: str,
                              trigger_close: float) -> bool:
    """
    检查突破是否超越了PT瑕疵高点和结构内的前期小高点。

    如果PT有实体穿越瑕疵，DN需要超越该穿越高点。
    如果平台位前有小高点，最好也突破。

    Returns:
        True: 已超越所有关键高/低点
        False: 未超越
        None: 无需检查（无瑕疵）
    """
    start = structure.structure_start_idx
    end = structure.structure_end_idx
    struct_df = df.iloc[start: end + 1]

    if direction == 'bullish':
        # 收集需要超越的高点
        check_prices = []

        # PT瑕疵: 如果阻力位有实体穿越，找穿越段的最高价
        if (platform.resistance_body_penetrations > 0 and
                platform.resistance_price > 0):
            pen_high = struct_df['High'].max()  # 简化：取结构内最高点
            check_prices.append(pen_high)

        # 结构内的前期小高点（P95之上的高点）
        highs = struct_df['High'].values
        if len(highs) > 0:
            p95 = np.percentile(highs, 95)
            local_highs = highs[highs >= p95]
            if len(local_highs) > 0:
                check_prices.append(float(local_highs.max()))

        if not check_prices:
            return None  # 无需检查

        max_check = max(check_prices)
        return trigger_close > max_check

    elif direction == 'bearish':
        check_prices = []

        if (platform.support_body_penetrations > 0 and
                platform.support_price > 0):
            pen_low = struct_df['Low'].min()
            check_prices.append(pen_low)

        lows = struct_df['Low'].values
        if len(lows) > 0:
            p5 = np.percentile(lows, 5)
            local_lows = lows[lows <= p5]
            if len(local_lows) > 0:
                check_prices.append(float(local_lows.min()))

        if not check_prices:
            return None

        min_check = min(check_prices)
        return trigger_close < min_check

    return None


def _find_best_breakout(df: pd.DataFrame, scan_start: int, scan_end: int,
                        range_high: float, range_low: float,
                        struct_atr: float, max_merged: int) -> tuple:
    """
    综合评估单根和合并K线(最多max_merged根)，返回最强突破。

    同时检测向上和向下，同时评估单根/2根/3根合并。
    向上优先于向下，同方向内取body最大的。

    Returns:
        (trigger_idx, direction, merged_count) 或 (-1, '', 1)
    """
    bulls = []   # (trigger_idx, merged_count, body)
    bears = []

    for i in range(scan_start, scan_end + 1):
        row_i = df.iloc[i]

        # ── 单根 ──
        body = abs(row_i['Close'] - row_i['Open'])
        if body > struct_atr * 0.3:
            if row_i['Close'] > range_high:
                bulls.append((i, 1, body))
            elif row_i['Close'] < range_low:
                bears.append((i, 1, body))

        # ── 合并 2~max_merged 根 ──
        for n in range(2, max_merged + 1):
            ms = i - n + 1
            if ms < scan_start:
                continue
            m_open = df.iloc[ms]['Open']
            m_close = row_i['Close']
            m_body = abs(m_close - m_open)
            if m_close > m_open and m_close > range_high:
                bulls.append((i, n, m_body))
            elif m_close < m_open and m_close < range_low:
                bears.append((i, n, m_body))

    # 向上优先；同方向内: 单根优先（可获S/A），合并兜底
    if bulls:
        singles = [c for c in bulls if c[1] == 1]
        if singles:
            best = max(singles, key=lambda x: x[2])
        else:
            best = max(bulls, key=lambda x: x[2])
        return (best[0], 'bullish', best[1])

    if bears:
        singles = [c for c in bears if c[1] == 1]
        if singles:
            best = max(singles, key=lambda x: x[2])
        else:
            best = max(bears, key=lambda x: x[2])
        return (best[0], 'bearish', best[1])

    return (-1, '', 1)
