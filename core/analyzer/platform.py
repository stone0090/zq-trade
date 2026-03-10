"""
PT 平台位/颈线位检测

在DL识别出的盘整结构内，同时检测上平台（阻力位）和下平台（支撑位）。
要求3次以上有效测试，测试间隔理想20根以上K线。
影线/实体穿越分级评分。DN确定方向后激活对应平台。
"""
import numpy as np
import pandas as pd

from core.types import AnalyzerConfig, PlatformResult, GradeScore, StructureResult
from core.utils.helpers import calc_atr, price_clustering, candle_body


def analyze_platform(df: pd.DataFrame,
                     structure: StructureResult,
                     config: AnalyzerConfig = None,
                     market: str = 'cn') -> PlatformResult:
    """
    PT 平台位/颈线位分析。

    同时检测上平台(阻力)和下平台(支撑)，分别评分存储。
    A股(market='cn')只检测阻力位，跳过支撑位。
    激活平台的选择由后续DN方向决定。
    """
    if config is None:
        config = AnalyzerConfig()

    result = PlatformResult()

    if not structure.passed and structure.kline_count == 0:
        result.reasoning.append("DL未检测到结构，跳过PT分析")
        return result

    start = structure.structure_start_idx
    end = structure.structure_end_idx
    struct_df = df.iloc[start: end + 1]

    if len(struct_df) < 10:
        result.reasoning.append("结构区间数据不足，无法检测平台位")
        return result

    # 计算 ATR 作为基准
    atr_series = calc_atr(struct_df)
    base_atr = atr_series.mean()
    if base_atr <= 0:
        base_atr = (struct_df['High'] - struct_df['Low']).mean()

    bin_width = base_atr * config.pt_bin_width_atr_ratio
    if bin_width <= 0:
        bin_width = 0.01

    tolerance = base_atr * config.pt_touch_tolerance_atr_ratio

    # ─── 1. 生成候选平台位 ───
    highs = struct_df['High'].values
    lows = struct_df['Low'].values
    high_threshold = np.percentile(highs, 70)
    low_threshold = np.percentile(lows, 30)

    upper_highs = highs[highs >= high_threshold]
    lower_lows = lows[lows <= low_threshold]

    resistance_candidates = price_clustering(upper_highs, bin_width, top_n=5)
    support_candidates = price_clustering(lower_lows, bin_width, top_n=5)

    all_candidates = []
    for price, freq in resistance_candidates:
        all_candidates.append((price, freq, 'resistance'))
    for price, freq in support_candidates:
        all_candidates.append((price, freq, 'support'))
    result.all_candidates = [(p, f) for p, f, _ in all_candidates]

    # ─── 2. 分别找出最佳阻力位和最佳支撑位 ───
    best_resistance = _find_best_candidate(
        struct_df, resistance_candidates, 'resistance', tolerance,
        config.pt_min_touch_interval, base_atr, config
    )
    # A股只检测阻力位，跳过支撑位
    if market == 'cn':
        best_support = None
    else:
        best_support = _find_best_candidate(
            struct_df, support_candidates, 'support', tolerance,
            config.pt_min_touch_interval, base_atr, config
        )

    # ─── 3. 存储阻力位结果 ───
    if best_resistance:
        result.resistance_price = round(best_resistance['price'], 3)
        result.resistance_zone_high = round(best_resistance['zone_high'], 3)
        result.resistance_zone_low = round(best_resistance['zone_low'], 3)
        result.resistance_touches = best_resistance['touches']
        result.resistance_touch_count = best_resistance['touch_count']
        result.resistance_penetrations = best_resistance['penetrations']
        result.resistance_shadow_penetrations = best_resistance['shadow_pens']
        result.resistance_body_penetrations = best_resistance['body_pens']
        result.resistance_post_pen_tests = best_resistance['post_pen_tests']
        score, reason = _grade_platform(best_resistance, config)
        result.resistance_score = score
        if reason:
            result.reasoning.append(f"阻力位{score}: {reason}")

    # ─── 4. 存储支撑位结果 ───
    if best_support:
        result.support_price = round(best_support['price'], 3)
        result.support_zone_high = round(best_support['zone_high'], 3)
        result.support_zone_low = round(best_support['zone_low'], 3)
        result.support_touches = best_support['touches']
        result.support_touch_count = best_support['touch_count']
        result.support_penetrations = best_support['penetrations']
        result.support_shadow_penetrations = best_support['shadow_pens']
        result.support_body_penetrations = best_support['body_pens']
        result.support_post_pen_tests = best_support['post_pen_tests']
        score, reason = _grade_platform(best_support, config)
        result.support_score = score
        if reason:
            result.reasoning.append(f"支撑位{score}: {reason}")

    # ─── 5. 尾部能量释放检测 ───
    tail_window = min(config.pt_tail_window, len(struct_df))
    tail_df = struct_df.iloc[-tail_window:]
    avg_vol = struct_df['Volume'].mean()

    has_tail_energy = False
    for _, row in tail_df.iterrows():
        krange = row['High'] - row['Low']
        if (krange > base_atr * config.pt_tail_energy_range_mult and
                row['Volume'] > avg_vol * config.pt_tail_energy_vol_mult):
            has_tail_energy = True
            break
    result.has_tail_energy = has_tail_energy

    # ─── 6. 暂时不设置激活平台（等DN方向确定后由 activate_platform 设置）
    r_score = result.resistance_score.value if best_resistance else 0
    s_score = result.support_score.value if best_support else 0

    if r_score >= s_score and best_resistance:
        _set_active(result, 'resistance')
    elif best_support:
        _set_active(result, 'support')
    elif best_resistance:
        _set_active(result, 'resistance')
    else:
        result.reasoning.append("未找到有效平台位（无足够触碰）")
        return result

    result.reasoning.append(
        f"阻力区间: {result.resistance_zone_low:.3f}~{result.resistance_zone_high:.3f} "
        f"(中心 {result.resistance_price:.3f}, "
        f"{result.resistance_touch_count}次测试/"
        f"影线穿{result.resistance_shadow_penetrations}次/"
        f"实体穿{result.resistance_body_penetrations}次, "
        f"{result.resistance_score})"
    )
    result.reasoning.append(
        f"支撑区间: {result.support_zone_low:.3f}~{result.support_zone_high:.3f} "
        f"(中心 {result.support_price:.3f}, "
        f"{result.support_touch_count}次测试/"
        f"影线穿{result.support_shadow_penetrations}次/"
        f"实体穿{result.support_body_penetrations}次, "
        f"{result.support_score})"
    )
    result.reasoning.append(f"ATR基准: {base_atr:.3f}")

    return result


def activate_platform(pt: PlatformResult, direction: str):
    """
    根据DN突破方向激活对应平台位，更新score/passed等字段。

    Args:
        pt: 平台位结果
        direction: 'bullish' → 激活阻力位, 'bearish' → 激活支撑位
    """
    if direction == 'bullish' and pt.resistance_price > 0:
        _set_active(pt, 'resistance')
        pt.reasoning.append(f"向上突破 → 激活阻力平台 {pt.resistance_price:.3f}")
    elif direction == 'bearish' and pt.support_price > 0:
        _set_active(pt, 'support')
        pt.reasoning.append(f"向下突破 → 激活支撑平台 {pt.support_price:.3f}")
    # else: keep current active


def _set_active(pt: PlatformResult, pt_type: str):
    """将指定类型的平台设为激活状态"""
    if pt_type == 'resistance':
        pt.platform_price = pt.resistance_price
        pt.platform_zone_high = pt.resistance_zone_high
        pt.platform_zone_low = pt.resistance_zone_low
        pt.platform_type = 'resistance'
        pt.touch_count = pt.resistance_touch_count
        pt.touch_points = pt.resistance_touches
        pt.penetration_count = pt.resistance_penetrations
        pt.score = pt.resistance_score
    else:
        pt.platform_price = pt.support_price
        pt.platform_zone_high = pt.support_zone_high
        pt.platform_zone_low = pt.support_zone_low
        pt.platform_type = 'support'
        pt.touch_count = pt.support_touch_count
        pt.touch_points = pt.support_touches
        pt.penetration_count = pt.support_penetrations
        pt.score = pt.support_score

    pt.passed = pt.score.value >= GradeScore.B.value


def _find_best_candidate(struct_df, candidates, pt_type, tolerance,
                         min_interval, base_atr, config):
    """
    从候选列表中找出最佳平台位。

    区间宽度动态决定: 基于实际触碰点价格分布 + K线实体均宽作为padding。
    """
    best = None
    best_score = float('-inf')

    avg_body = (struct_df['Close'] - struct_df['Open']).abs().mean()

    # 穿越判定用更宽容忍带（2倍触碰容忍），"稍微超过"不算穿越
    pen_tolerance = tolerance * 2

    for center_price, freq in candidates:
        touches = _count_touches(struct_df, center_price, tolerance,
                                 min_interval, pt_type)
        shadow_pens, body_pens, pen_events = _count_penetrations_detailed(
            struct_df, center_price, pen_tolerance, pt_type
        )

        # 实体穿越后的有效测试次数
        post_pen_tests = 0
        if body_pens > 0:
            post_pen_tests = _count_post_penetration_tests(
                struct_df, center_price, pen_tolerance, pt_type
            )

        # 首次接近PT时的过冲检测
        first_overshoot_atr = _detect_first_approach_overshoot(
            struct_df, center_price, tolerance, base_atr, pt_type
        )

        total_pens = shadow_pens + body_pens
        # 候选评分: 测试次数优先，穿越是瑕疵
        # 达到最低触碰数的候选大幅加分，确保优先于未达标候选
        candidate_score = len(touches) * 3 - body_pens * 2 - shadow_pens * 1
        if len(touches) >= config.pt_min_touch_count:
            candidate_score += 100

        # 动态区间
        if touches:
            touch_prices = [tp[1] for tp in touches]
            tp_max = max(touch_prices)
            tp_min = min(touch_prices)
            zone_high = max(tp_max, center_price) + avg_body * 0.5
            zone_low = min(tp_min, center_price) - avg_body * 0.5
        else:
            zone_high = center_price + avg_body
            zone_low = center_price - avg_body

        # 计算测试间隔的均匀性
        avg_interval = _calc_avg_interval(touches)

        if candidate_score > best_score:
            best_score = candidate_score
            best = {
                'price': center_price,
                'zone_high': zone_high,
                'zone_low': zone_low,
                'touches': touches,
                'touch_count': len(touches),
                'penetrations': total_pens,
                'shadow_pens': shadow_pens,
                'body_pens': body_pens,
                'post_pen_tests': post_pen_tests,
                'pen_events': pen_events,
                'avg_interval': avg_interval,
                'first_overshoot_atr': first_overshoot_atr,
                'freq': freq,
                'type': pt_type,
            }

    if best and best['touch_count'] == 0:
        return None

    return best


def _grade_platform(candidate: dict, config: AnalyzerConfig) -> tuple:
    """
    根据测试次数、穿越类型、间隔分布评分。

    S: ≥3次测试，实体未穿越 + 测试事件无过高点 + 测试间隔充分
    A: ≥3次测试，实体未穿越但有测试过高点或间隔偏短；
       或实体穿越后有≥2次测试恢复
    B: 多次穿越但仍有测试；或间隔不够
    C: 不足3次测试；或实体穿越后恢复不足

    Returns:
        (GradeScore, reason_str)
    """
    tc = candidate['touch_count']
    shadow_pens = candidate['shadow_pens']
    body_pens = candidate['body_pens']
    post_pen_tests = candidate['post_pen_tests']
    avg_interval = candidate['avg_interval']
    first_overshoot = candidate.get('first_overshoot_atr', 0.0)
    min_required = config.pt_min_touch_count  # 3
    ideal_interval = config.pt_min_touch_interval  # 20

    # 不足3次测试 → C
    if tc < min_required:
        return GradeScore.C, f"仅{tc}次测试（不足{min_required}次）"

    # ≥3次测试 + 实体未穿越
    if body_pens == 0:
        # 测试事件有过高点(≥0.25 ATR) → A
        if first_overshoot >= config.pt_first_overshoot_threshold:
            detail = f"首测过高点{first_overshoot:.2f}ATR"
            if shadow_pens > 0:
                detail += f"，影线穿越{shadow_pens}次"
            return GradeScore.A, detail
        # 间隔充分 → S
        if avg_interval >= ideal_interval:
            detail = f"影线穿越{shadow_pens}次（不影响评级）" if shadow_pens > 0 else ""
            return GradeScore.S, detail
        # 间隔偏短 → A
        detail = f"间隔{avg_interval:.0f}根偏短"
        if shadow_pens > 0:
            detail += f"，影线穿越{shadow_pens}次"
        return GradeScore.A, detail

    # 实体穿越后又有≥2次测试恢复 → A
    if body_pens > 0 and post_pen_tests >= 2:
        return GradeScore.A, f"实体穿越{body_pens}次，但穿越后有{post_pen_tests}次测试恢复"

    # 实体穿越后不足2次测试 → C（恢复不够，平台无效）
    if body_pens > 0 and post_pen_tests < 2:
        return GradeScore.C, f"实体穿越{body_pens}次，穿越后仅{post_pen_tests}次测试（恢复不够）"

    # 有穿越但还有测试；或间隔不够
    if tc >= min_required:
        reasons = []
        if body_pens > 0:
            reasons.append(f"实体穿越{body_pens}次")
        if shadow_pens > 0:
            reasons.append(f"影线穿越{shadow_pens}次")
        if avg_interval < ideal_interval:
            reasons.append(f"测试间隔{avg_interval:.0f}根偏短")
        return GradeScore.B, "、".join(reasons) if reasons else "穿越较多"

    return GradeScore.C, f"仅{tc}次测试"


def _count_touches(struct_df: pd.DataFrame, price: float,
                   tolerance: float, min_interval: int,
                   pt_type: str) -> list:
    """
    统计有效触碰次数。

    对于支撑平台: K线的Low接近平台价且收盘在平台价之上（从下方弹起）
    对于阻力平台: K线的High接近平台价且收盘在平台价之下（从上方回落）
    """
    touches = []
    last_touch_pos = -min_interval - 1

    for i in range(len(struct_df)):
        row = struct_df.iloc[i]
        pos = i

        if pos - last_touch_pos < min_interval:
            continue

        if pt_type == 'support':
            if abs(row['Low'] - price) <= tolerance and row['Close'] >= price:
                touches.append((i, float(row['Low']), 'support'))
                last_touch_pos = pos
        else:
            if abs(row['High'] - price) <= tolerance and row['Close'] <= price:
                touches.append((i, float(row['High']), 'resistance'))
                last_touch_pos = pos

    return touches


def _count_penetrations_detailed(struct_df: pd.DataFrame, price: float,
                                 tolerance: float, pt_type: str) -> tuple:
    """
    分别统计影线穿越和实体穿越次数。

    影线穿越: 影线超过平台位但实体没有穿过
    实体穿越: 实体完全穿过平台位（连续穿透段只计一次事件）

    注意：结构起始处价格自然高于/低于PT的K线不算穿越。
    只有价格先回到PT附近，再次突破PT，才算真正的穿越。

    Returns:
        (shadow_pen_count, body_pen_count, pen_event_indices)
    """
    shadow_count = 0
    body_count = 0
    pen_events = []
    was_body_pen = False
    seen_near_pt = False  # 价格是否曾到过PT附近

    for i in range(len(struct_df)):
        row = struct_df.iloc[i]
        body_low, body_high = candle_body(row['Open'], row['Close'])

        if pt_type == 'support':
            # 影线穿越: Low穿过了但实体没有
            shadow_pen = row['Low'] < price - tolerance and body_low >= price - tolerance
            # 实体穿越: 实体完全在平台价下方
            body_pen = body_high < price - tolerance
        else:
            # 影线穿越: High穿过了但实体没有
            shadow_pen = row['High'] > price + tolerance and body_high <= price + tolerance
            # 实体穿越: 实体完全在平台价上方
            body_pen = body_low > price + tolerance

        # 价格曾到过PT附近（非穿越状态），才开始计穿越
        if not body_pen:
            seen_near_pt = True

        if shadow_pen and seen_near_pt:
            shadow_count += 1

        if body_pen and not was_body_pen and seen_near_pt:
            body_count += 1
            pen_events.append(i)
        was_body_pen = body_pen

    return shadow_count, body_count, pen_events


def _count_post_penetration_tests(struct_df: pd.DataFrame, price: float,
                                  tolerance: float, pt_type: str) -> int:
    """
    统计最后一次实体穿越之后的有效测试次数。
    与 _count_penetrations_detailed 保持一致：排除结构起始高位K线。
    """
    # 找到最后一次实体穿越的位置（排除结构起始处自然偏离）
    last_pen_idx = -1
    was_body_pen = False
    seen_near_pt = False
    for i in range(len(struct_df)):
        row = struct_df.iloc[i]
        body_low, body_high = candle_body(row['Open'], row['Close'])

        if pt_type == 'support':
            body_pen = body_high < price - tolerance
        else:
            body_pen = body_low > price + tolerance

        if not body_pen:
            seen_near_pt = True

        if body_pen and not was_body_pen and seen_near_pt:
            last_pen_idx = i
        was_body_pen = body_pen

    if last_pen_idx < 0:
        return 0

    # 穿越后的有效测试次数
    count = 0
    for i in range(last_pen_idx + 1, len(struct_df)):
        row = struct_df.iloc[i]
        if pt_type == 'support':
            if abs(row['Low'] - price) <= tolerance and row['Close'] >= price:
                count += 1
        else:
            if abs(row['High'] - price) <= tolerance and row['Close'] <= price:
                count += 1

    return count


def _calc_avg_interval(touches: list) -> float:
    """计算相邻测试之间的平均间隔K线数。"""
    if len(touches) < 2:
        return 0.0
    intervals = []
    for i in range(1, len(touches)):
        intervals.append(touches[i][0] - touches[i - 1][0])
    return sum(intervals) / len(intervals) if intervals else 0.0


def _detect_first_approach_overshoot(struct_df: pd.DataFrame, price: float,
                                     tolerance: float, base_atr: float,
                                     pt_type: str) -> float:
    """
    检测价格首次从对侧接近PT时是否有显著过冲。

    对于阻力位：找第一根收盘低于PT的K线(价格在PT下方)，
    然后找从此位置起第一根High触及PT区域的K线，
    再检查该K线及随后几根K线的最大High是否显著超过PT+tolerance。

    对于支撑位：对称逻辑。

    Returns:
        过冲幅度(ATR为单位)，0表示无显著过冲
    """
    if base_atr <= 0 or len(struct_df) < 3:
        return 0.0

    # 首次接近后检查的窗口大小（包含首根）
    approach_window = 3
    # 结构开头的"定位期"不算首次接近过冲：前2根K线是结构初始定位，
    # 此时的价格波动是建立结构范围的自然过程，不是真正的PT测试
    settle_period = 2

    if pt_type == 'resistance':
        # 1. 找第一根Close < price的K线
        first_below_idx = -1
        for i in range(len(struct_df)):
            if struct_df.iloc[i]['Close'] < price:
                first_below_idx = i
                break
        if first_below_idx < 0:
            return 0.0

        # 2. 从first_below起，找第一根High触及PT区域的K线
        for i in range(first_below_idx, len(struct_df)):
            row = struct_df.iloc[i]
            if row['High'] >= price - tolerance:
                # 结构开头定位期内的波动不算过冲
                if i < settle_period:
                    return 0.0
                # 检查该K线及随后几根K线的最大过冲(相对PT+tolerance)
                max_overshoot = 0.0
                end_w = min(i + approach_window, len(struct_df))
                for j in range(i, end_w):
                    ov = struct_df.iloc[j]['High'] - (price + tolerance)
                    if ov > max_overshoot:
                        max_overshoot = ov
                if max_overshoot > 0:
                    return max_overshoot / base_atr
                return 0.0
    else:
        # 支撑位：对称逻辑
        first_above_idx = -1
        for i in range(len(struct_df)):
            if struct_df.iloc[i]['Close'] > price:
                first_above_idx = i
                break
        if first_above_idx < 0:
            return 0.0

        for i in range(first_above_idx, len(struct_df)):
            row = struct_df.iloc[i]
            if row['Low'] <= price + tolerance:
                # 结构开头定位期内的波动不算过冲
                if i < settle_period:
                    return 0.0
                max_overshoot = 0.0
                end_w = min(i + approach_window, len(struct_df))
                for j in range(i, end_w):
                    ov = (price - tolerance) - struct_df.iloc[j]['Low']
                    if ov > max_overshoot:
                        max_overshoot = ov
                if max_overshoot > 0:
                    return max_overshoot / base_atr
                return 0.0

    return 0.0


def _detect_touch_overshoots(struct_df: pd.DataFrame, touches: list,
                             price: float, base_atr: float,
                             pt_type: str) -> float:
    """
    检测每个测试事件附近是否有过高点（影线显著超过PT价格）。

    对每个touch前后各看3根K线，找到最大过冲。
    过冲从PT价格计算（非PT+tolerance），更符合人工判断逻辑。

    Returns:
        最大过冲幅度(ATR为单位)，0表示无过冲
    """
    if base_atr <= 0 or not touches:
        return 0.0

    window = 3
    max_overshoot_atr = 0.0

    for touch_idx, _, _ in touches:
        start = max(0, touch_idx - window)
        end = min(len(struct_df), touch_idx + window + 1)
        for j in range(start, end):
            row = struct_df.iloc[j]
            if pt_type == 'resistance':
                overshoot = row['High'] - price
            else:
                overshoot = price - row['Low']
            if overshoot > 0:
                ov_atr = overshoot / base_atr
                if ov_atr > max_overshoot_atr:
                    max_overshoot_atr = ov_atr

    return max_overshoot_atr
