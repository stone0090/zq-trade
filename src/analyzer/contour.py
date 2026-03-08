"""
LK 轮廓质量评估

衡量盘整结构的形态质量：波浪规则性 + 边界平滑度 + K线均匀性。
好的调整区间应呈规则的波浪形，上下边界平滑、K线大小均匀。
"""
import numpy as np
import pandas as pd

from src.analyzer.base import AnalyzerConfig, ContourResult, GradeScore, StructureResult
from src.utils.helpers import calc_atr


def analyze_contour(df: pd.DataFrame,
                    structure: StructureResult,
                    config: AnalyzerConfig = None) -> ContourResult:
    """
    LK 轮廓质量分析。

    算法:
    1. 上下轨道平滑度（一阶差分标准差）
    2. K线振幅变异系数（均匀性）
    3. 异常K线占比
    4. 波浪规则性（上下轨道周期一致性）
    5. 综合加权评分
    """
    if config is None:
        config = AnalyzerConfig()

    result = ContourResult()

    if structure.kline_count == 0:
        result.reasoning.append("DL未检测到结构，跳过LK分析")
        return result

    start = structure.structure_start_idx
    end = structure.structure_end_idx
    struct_df = df.iloc[start: end + 1]

    if len(struct_df) < config.lk_rolling_window + 2:
        result.reasoning.append("结构区间数据不足，无法评估轮廓")
        return result

    window = config.lk_rolling_window

    # ─── 1. 上下轨道计算与平滑度 ───
    upper_band = struct_df['High'].rolling(window=window, min_periods=1).max()
    lower_band = struct_df['Low'].rolling(window=window, min_periods=1).min()

    # 一阶差分标准差
    upper_diff_std = np.std(np.diff(upper_band.dropna().values))
    lower_diff_std = np.std(np.diff(lower_band.dropna().values))

    mean_price = struct_df['Close'].mean()
    upper_smoothness = upper_diff_std / mean_price if mean_price > 0 else 0
    lower_smoothness = lower_diff_std / mean_price if mean_price > 0 else 0
    avg_smoothness = (upper_smoothness + lower_smoothness) / 2

    result.upper_smoothness = round(upper_smoothness, 6)
    result.lower_smoothness = round(lower_smoothness, 6)

    # ─── 2. K线振幅均匀性 ───
    ranges = (struct_df['High'] - struct_df['Low']).values
    range_mean = ranges.mean()
    range_std = ranges.std()
    range_cv = range_std / range_mean if range_mean > 0 else 0
    result.range_cv = round(range_cv, 4)

    # ─── 3. 异常K线检测 ───
    abnormal_threshold = range_mean + config.lk_abnormal_std_mult * range_std
    abnormal_mask = ranges > abnormal_threshold
    abnormal_count = int(abnormal_mask.sum())
    abnormal_ratio = abnormal_count / len(ranges) if len(ranges) > 0 else 0

    result.abnormal_count = abnormal_count
    result.abnormal_ratio = round(abnormal_ratio, 4)

    # ─── 4. 波浪规则性评估 ───
    # 用收盘价的局部极值点间距的规则性来衡量波浪是否有节奏
    wave_regularity = _calc_wave_regularity(struct_df['Close'].values)

    # ─── 5. 宽度评估 ───
    width_pct = (structure.range_high - structure.range_low) / mean_price if mean_price > 0 else 0
    result.width_pct = round(width_pct, 4)
    result.is_narrow = width_pct < config.lk_narrow_threshold

    # ─── 6. 综合质量分计算 ───
    # 各指标归一化到 [0,1]（越小越好的指标取反）
    smoothness_norm = min(avg_smoothness / 0.01, 1.0)
    cv_norm = min(range_cv / 1.0, 1.0)
    abnormal_norm = min(abnormal_ratio / 0.15, 1.0)

    # 波浪规则性已经是 [0,1]，越大越好
    # 融合进综合分：平滑度35% + 均匀性30% + 异常K线15% + 波浪规则性20%
    quality_score = (0.35 * (1 - smoothness_norm) +
                     0.30 * (1 - cv_norm) +
                     0.15 * (1 - abnormal_norm) +
                     0.20 * wave_regularity)

    result.quality_score = round(quality_score, 4)

    # ─── 6b. 新增视觉特征检测 ───

    # 对称性：前半段和后半段收盘价模式的相关性
    symmetry = _calc_symmetry(struct_df['Close'].values)
    result.symmetry_score = round(symmetry, 4)

    # 尾部破位：检测尾部1/4是否跌破前3/4的最低点
    tail_break, tail_break_pct = _detect_tail_break(struct_df)
    result.tail_break = tail_break
    result.tail_break_pct = round(tail_break_pct, 4)

    # 中间段密集度：中间1/2的K线间距是否紧密
    density = _calc_density(struct_df)
    result.density_score = round(density, 4)

    # ─── 7. 评分（规则驱动，匹配视觉感知判断） ───
    n = len(struct_df)
    is_short = n < 90  # 短结构的密集度/异常指标不可靠，放宽

    # 判定因子
    has_severe_tail_break = tail_break and tail_break_pct > 2.0
    has_mild_tail_break = tail_break and tail_break_pct <= 2.0
    has_good_symmetry = symmetry >= 0.55
    has_poor_density = density < 0.40 and not is_short
    abnormal_limit = 0.07 if is_short else 0.05
    has_many_abnormal = abnormal_ratio > abnormal_limit

    # 决策树
    deficiencies = []
    if has_severe_tail_break:
        # 尾部严重破位 → 最高B
        result.score = GradeScore.B
        deficiencies.append(f"尾部破位({tail_break_pct:.1f}%)")
    elif not tail_break and not has_poor_density and not has_many_abnormal:
        # 无明显问题 → S
        result.score = GradeScore.S
    elif has_mild_tail_break and has_good_symmetry:
        # 轻度破位但整体对称 → A
        result.score = GradeScore.A
        deficiencies.append(f"尾部稍凌厉({tail_break_pct:.1f}%)")
    elif has_mild_tail_break and not has_good_symmetry and abnormal_ratio > 0.07:
        # 破位 + 不对称 + 异常多 → C
        result.score = GradeScore.C
        deficiencies.append(f"尾部破位({tail_break_pct:.1f}%)")
        deficiencies.append(f"对称性差({symmetry:.2f})")
        deficiencies.append(f"异常K线{abnormal_count}根")
    elif has_mild_tail_break:
        # 轻度破位 → B
        result.score = GradeScore.B
        deficiencies.append(f"尾部破位({tail_break_pct:.1f}%)")
        if not has_good_symmetry:
            deficiencies.append(f"对称性不足({symmetry:.2f})")
    elif (has_many_abnormal or has_poor_density) and has_good_symmetry:
        # 异常多/密集度差，但对称性好补救 → A
        result.score = GradeScore.A
        if has_many_abnormal:
            deficiencies.append(f"异常K线偏多({abnormal_count}根)")
        if has_poor_density:
            deficiencies.append(f"中间段松散")
        deficiencies.append(f"对称性尚可({symmetry:.2f})")
    elif has_many_abnormal or has_poor_density:
        # 异常多/密集度差，无对称补救 → B
        result.score = GradeScore.B
        if has_many_abnormal:
            deficiencies.append(f"异常K线{abnormal_count}根({abnormal_ratio:.1%})")
        if has_poor_density:
            deficiencies.append(f"中间段松散(密集度{density:.2f})")
    else:
        # 其他小瑕疵 → A
        result.score = GradeScore.A
        if smoothness_norm > 0.5:
            deficiencies.append(f"边界平滑度偏低")
        if cv_norm > 0.5:
            deficiencies.append(f"K线振幅不均匀")
        if wave_regularity < 0.5:
            deficiencies.append(f"波浪规则性差")

    reason = "、".join(deficiencies) if deficiencies else ""
    if result.score == GradeScore.S:
        result.reasoning.append(f"形态工整，无明显缺陷 → S")
    else:
        result.reasoning.append(f"{reason} → {result.score.name}")

    result.passed = result.score.value >= GradeScore.B.value

    # 补充信息
    result.reasoning.append(
        f"对称性: {symmetry:.2f}，密集度: {density:.2f}，"
        f"尾部破位: {'是('+str(tail_break_pct)+'%)' if tail_break else '否'}，"
        f"振幅CV: {range_cv:.3f}，异常K线: {abnormal_count}根({abnormal_ratio:.1%})，"
        f"波浪规则性: {wave_regularity:.2f}"
    )
    if result.is_narrow:
        result.reasoning.append(f"窄幅结构（振幅{width_pct:.2%}），评分阈值已上调")

    return result


def _calc_wave_regularity(close: np.ndarray) -> float:
    """
    评估收盘价波浪的规则性（节奏感）。

    通过检测局部极值点之间的间距是否均匀来衡量。
    间距变异系数越小说明波浪越规则。

    Returns:
        regularity: 0.0~1.0，越大越规则
    """
    if len(close) < 10:
        return 0.5  # 数据不足，给中性分

    # 找局部极值点（前后各3个点的窗口）
    order = 3
    peaks = []
    troughs = []

    for i in range(order, len(close) - order):
        window = close[i - order: i + order + 1]
        if np.isnan(window).any():
            continue
        if close[i] == window.max() and close[i] > close[i - 1] and close[i] > close[i + 1]:
            peaks.append(i)
        if close[i] == window.min() and close[i] < close[i - 1] and close[i] < close[i + 1]:
            troughs.append(i)

    # 合并所有极值点并按位置排序
    all_extremes = sorted(peaks + troughs)

    if len(all_extremes) < 3:
        return 0.5  # 极值点太少，给中性分

    # 计算相邻极值点的间距
    intervals = np.diff(all_extremes)

    if len(intervals) < 2:
        return 0.5

    # 间距的变异系数 → 越小越规则
    interval_mean = intervals.mean()
    interval_std = intervals.std()
    interval_cv = interval_std / interval_mean if interval_mean > 0 else 1.0

    # CV 映射到 [0,1]：CV=0 → 1.0(完美规则), CV>=1.5 → 0.0
    regularity = max(0.0, 1.0 - interval_cv / 1.5)

    return round(regularity, 4)


def _calc_symmetry(close: np.ndarray) -> float:
    """
    评估前后半段的对称性。

    将收盘价分为前后两半，将后半段反转，计算相关系数。
    高相关性说明形态对称。

    Returns:
        symmetry: 0.0~1.0
    """
    n = len(close)
    if n < 20:
        return 0.5

    half = n // 2
    first_half = close[:half]
    second_half = close[n - half:][::-1]  # 反转后半段

    # 归一化
    f_norm = (first_half - first_half.mean()) / (first_half.std() + 1e-8)
    s_norm = (second_half - second_half.mean()) / (second_half.std() + 1e-8)

    # 相关系数
    corr = np.corrcoef(f_norm, s_norm)[0, 1]
    if np.isnan(corr):
        return 0.5

    # 映射到 [0, 1]：corr=-1→0, corr=0→0.5, corr=1→1.0
    return max(0.0, min(1.0, (corr + 1) / 2))


def _detect_tail_break(struct_df: pd.DataFrame) -> tuple:
    """
    检测尾部是否破位（突破前期低点）。

    将结构分为前3/4和后1/4，检查后1/4的最低价是否低于前3/4的最低价。

    Returns:
        (is_break: bool, break_pct: float)
    """
    n = len(struct_df)
    if n < 20:
        return False, 0.0

    split = int(n * 0.75)
    front = struct_df.iloc[:split]
    tail = struct_df.iloc[split:]

    front_low = front['Low'].min()
    tail_low = tail['Low'].min()
    mean_price = struct_df['Close'].mean()

    if tail_low < front_low and mean_price > 0:
        break_pct = (front_low - tail_low) / mean_price * 100
        return True, break_pct

    return False, 0.0


def _calc_density(struct_df: pd.DataFrame) -> float:
    """
    评估中间段的K线密集度。

    密集度通过中间1/2区间内K线的平均振幅占整体振幅的比率来衡量。
    振幅越小（相对于整体），说明K线越紧凑密集。

    Returns:
        density: 0.0~1.0，越大越密集
    """
    n = len(struct_df)
    if n < 20:
        return 0.5

    q1 = n // 4
    q3 = q1 * 3
    mid_df = struct_df.iloc[q1:q3]

    mid_ranges = (mid_df['High'] - mid_df['Low']).values
    all_ranges = (struct_df['High'] - struct_df['Low']).values

    mid_avg = mid_ranges.mean()
    all_avg = all_ranges.mean()

    if all_avg <= 0:
        return 0.5

    # 中间段振幅越小（相对整体），密集度越高
    ratio = mid_avg / all_avg
    # ratio < 0.5 → 很密集(1.0), ratio > 1.5 → 很松散(0.0)
    density = max(0.0, min(1.0, 1.0 - (ratio - 0.5) / 1.0))

    return round(density, 4)
