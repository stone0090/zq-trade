"""
LK 轮廓质量评估

衡量盘整结构的"平整度"和"均匀度"。
平滑、窄幅的盘整 > 尖锐、波动大的盘整。
"""
import numpy as np
import pandas as pd

from src.analyzer.base import AnalyzerConfig, ContourResult, GradeScore, StructureResult


def analyze_contour(df: pd.DataFrame,
                    structure: StructureResult,
                    config: AnalyzerConfig = None) -> ContourResult:
    """
    LK 轮廓质量分析。

    算法:
    1. 上下轨道平滑度（一阶差分标准差）
    2. K线振幅变异系数
    3. 异常K线占比
    4. 综合加权评分
    """
    if config is None:
        config = AnalyzerConfig()

    result = ContourResult()

    if not structure.passed:
        result.reasoning.append("DL未通过，跳过LK分析")
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

    # ─── 4. 宽度评估 ───
    width_pct = (structure.range_high - structure.range_low) / mean_price if mean_price > 0 else 0
    result.width_pct = round(width_pct, 4)
    result.is_narrow = width_pct < config.lk_narrow_threshold

    # ─── 5. 综合质量分计算 ───
    # 各指标归一化到 [0,1]（越小越好的指标取反）
    # 平滑度: 经验上 0~0.01 为优秀范围
    smoothness_norm = min(avg_smoothness / 0.01, 1.0)
    # CV: 经验上 0~1 为正常范围
    cv_norm = min(range_cv / 1.0, 1.0)
    # 异常占比: 直接使用
    abnormal_norm = min(abnormal_ratio / 0.15, 1.0)  # 15%以上归一化到1

    quality_score = (config.lk_weight_smoothness * (1 - smoothness_norm) +
                     config.lk_weight_cv * (1 - cv_norm) +
                     config.lk_weight_abnormal * (1 - abnormal_norm))

    result.quality_score = round(quality_score, 4)

    # ─── 6. 评分 ───
    s_threshold = 0.80
    a_threshold = 0.60
    b_threshold = 0.34

    # 窄结构惩罚
    if result.is_narrow:
        s_threshold += config.lk_narrow_penalty
        a_threshold += config.lk_narrow_penalty
        b_threshold += config.lk_narrow_penalty

    if quality_score >= s_threshold:
        result.score = GradeScore.S
        result.reasoning.append(f"质量分 {quality_score:.2f}（≥{s_threshold:.2f}） → S")
    elif quality_score >= a_threshold:
        result.score = GradeScore.A
        result.reasoning.append(f"质量分 {quality_score:.2f}（≥{a_threshold:.2f}） → A")
    elif quality_score >= b_threshold:
        result.score = GradeScore.B
        result.reasoning.append(f"质量分 {quality_score:.2f}（≥{b_threshold:.2f}） → B")
    else:
        result.score = GradeScore.C
        result.reasoning.append(f"质量分 {quality_score:.2f}（<{b_threshold:.2f}） → C")

    result.passed = result.score.value >= GradeScore.B.value

    # 补充信息
    result.reasoning.append(
        f"平滑度: 上轨{upper_smoothness:.5f}/下轨{lower_smoothness:.5f}，"
        f"振幅CV: {range_cv:.3f}，异常K线: {abnormal_count}根({abnormal_ratio:.1%})"
    )
    if result.is_narrow:
        result.reasoning.append(f"窄幅结构（振幅{width_pct:.2%}），评分阈值已上调")

    return result
