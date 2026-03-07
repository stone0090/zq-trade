"""
PT 平台位/颈线位检测

在DL识别出的盘整结构内，同时检测上平台（阻力位）和下平台（支撑位）。
向上突破时关注阻力平台，向下突破时关注支撑平台。
DN确定方向后激活对应平台。
"""
import numpy as np
import pandas as pd

from src.analyzer.base import AnalyzerConfig, PlatformResult, GradeScore, StructureResult
from src.utils.helpers import calc_atr, price_clustering, candle_body


def analyze_platform(df: pd.DataFrame,
                     structure: StructureResult,
                     config: AnalyzerConfig = None) -> PlatformResult:
    """
    PT 平台位/颈线位分析。

    同时检测上平台(阻力)和下平台(支撑)，分别评分存储。
    激活平台的选择由后续DN方向决定。
    """
    if config is None:
        config = AnalyzerConfig()

    result = PlatformResult()

    if not structure.passed:
        result.reasoning.append("DL未通过，跳过PT分析")
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
    pen_tolerance = base_atr * 0.5  # 穿透判定用更宽的容忍度

    # ─── 1. 生成候选平台位 ───
    # 阻力位: 从结构上部区域的High聚类（取P70以上的High值）
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
        struct_df, resistance_candidates, 'resistance', tolerance, pen_tolerance,
        config.pt_min_touch_interval, base_atr, config
    )
    best_support = _find_best_candidate(
        struct_df, support_candidates, 'support', tolerance, pen_tolerance,
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
        result.resistance_score = _grade_platform(
            best_resistance['touch_count'],
            best_resistance['penetrations'],
            best_resistance.get('has_tail_energy', False)
        )

    # ─── 4. 存储支撑位结果 ───
    if best_support:
        result.support_price = round(best_support['price'], 3)
        result.support_zone_high = round(best_support['zone_high'], 3)
        result.support_zone_low = round(best_support['zone_low'], 3)
        result.support_touches = best_support['touches']
        result.support_touch_count = best_support['touch_count']
        result.support_penetrations = best_support['penetrations']
        result.support_score = _grade_platform(
            best_support['touch_count'],
            best_support['penetrations'],
            best_support.get('has_tail_energy', False)
        )

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
    # 此处先以较优的一侧作为临时值，供DN分析使用
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
        f"{result.resistance_touch_count}次触碰/{result.resistance_penetrations}次穿透, "
        f"{result.resistance_score})"
    )
    result.reasoning.append(
        f"支撑区间: {result.support_zone_low:.3f}~{result.support_zone_high:.3f} "
        f"(中心 {result.support_price:.3f}, "
        f"{result.support_touch_count}次触碰/{result.support_penetrations}次穿透, "
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


def _find_best_candidate(struct_df, candidates, pt_type, tolerance, pen_tolerance,
                         min_interval, base_atr, config):
    """
    从候选列表中找出最佳平台位。

    区间宽度动态决定: 基于实际触碰点价格分布 + K线实体均宽作为padding,
    使区间自然适应K线轮廓粗细。
    """
    best = None
    best_score = float('-inf')

    # K线实体均宽: 反映整体K线"粗细"
    avg_body = (struct_df['Close'] - struct_df['Open']).abs().mean()

    for center_price, freq in candidates:
        touches = _count_touches(struct_df, center_price, tolerance,
                                 min_interval, pt_type)
        penetrations = _count_penetrations(struct_df, center_price, pen_tolerance, pt_type)

        # 穿透视为对平台位的有效测试（中性偏正面），不重罚
        candidate_score = len(touches) * 3 + penetrations * 1

        # 动态区间: 触碰点实际分布范围 + 实体均宽作为padding
        if touches:
            touch_prices = [tp[1] for tp in touches]
            tp_max = max(touch_prices)
            tp_min = min(touch_prices)
            zone_high = max(tp_max, center_price) + avg_body * 0.5
            zone_low = min(tp_min, center_price) - avg_body * 0.5
        else:
            zone_high = center_price + avg_body
            zone_low = center_price - avg_body

        if candidate_score > best_score:
            best_score = candidate_score
            best = {
                'price': center_price,
                'zone_high': zone_high,
                'zone_low': zone_low,
                'touches': touches,
                'touch_count': len(touches),
                'penetrations': penetrations,
                'freq': freq,
                'type': pt_type,
            }

    if best and best['touch_count'] == 0:
        return None

    return best


def _grade_platform(touch_count: int, penetration_count: int,
                    has_tail_energy: bool) -> GradeScore:
    """
    根据触碰次数和穿透事件数评分。

    穿透(事件)视为对平台位的有效测试:
    - 穿透说明价格积极测试该关键位，按0.5权重计入有效接触
    - 穿透事件比例过高时降级
    """
    tc = touch_count
    pc = penetration_count
    # 有效接触 = 纯触碰 + 穿透*0.5
    effective = tc + pc * 0.5
    total_contacts = tc + pc
    pen_ratio = pc / total_contacts if total_contacts > 0 else 1.0

    if effective >= 5 and pc <= 1:
        return GradeScore.S
    elif effective >= 4 and pen_ratio <= 0.45:
        return GradeScore.A
    elif effective >= 2:
        return GradeScore.B
    else:
        return GradeScore.C


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


def _count_penetrations(struct_df: pd.DataFrame, price: float,
                        tolerance: float, pt_type: str) -> int:
    """
    统计穿过平台位的事件次数（连续穿透段只计一次）。

    对于支撑平台: 实体(body_high)完全收在平台价下方 → 一次穿透事件
    对于阻力平台: 实体(body_low)完全收在平台价上方 → 一次穿透事件
    """
    count = 0
    was_penetrating = False
    for _, row in struct_df.iterrows():
        body_low, body_high = candle_body(row['Open'], row['Close'])

        if pt_type == 'support':
            is_pen = body_high < price - tolerance
        else:
            is_pen = body_low > price + tolerance

        if is_pen and not was_penetrating:
            count += 1
        was_penetrating = is_pen

    return count
