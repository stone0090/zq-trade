"""
综合评分引擎

编排6个维度的分析流程，汇总评分，输出最终评级和操作建议。
"""
import pandas as pd
from datetime import datetime

from src.analyzer.base import (
    AnalyzerConfig, ScoreCard, GradeScore, ReleaseLevel, PassFail
)
from src.analyzer.structure import analyze_structure
from src.analyzer.platform import analyze_platform, activate_platform
from src.analyzer.contour import analyze_contour
from src.analyzer.squeeze import analyze_squeeze
from src.analyzer.momentum import analyze_momentum
from src.analyzer.release import analyze_release
from src.utils.helpers import clean_ohlcv


def run_full_analysis(df: pd.DataFrame,
                      symbol: str = "",
                      config: AnalyzerConfig = None) -> ScoreCard:
    """
    运行完整的六维分析流程。

    执行顺序: DL → PT → LK → TY → DN → SF
    DL 不通过则提前终止。
    """
    if config is None:
        config = AnalyzerConfig()

    # 数据清洗
    df = clean_ohlcv(df)

    card = ScoreCard()
    card.symbol = symbol
    card.analysis_time = datetime.now()
    card.total_klines = len(df)

    if len(df) > 0:
        card.data_start = df.index[0]
        card.data_end = df.index[-1]

    # ─── 1. DL 独立结构 ───
    dl = analyze_structure(df, config)
    card.dl_result = dl

    if not dl.passed:
        card.early_terminated = True
        card.early_terminate_reason = "独立结构(DL)不满足条件，后续维度未分析"
        card.disqualify_reasons.append(f"DL: {dl.reasoning[0] if dl.reasoning else '不通过'}")
        _finalize_card(card, config)
        return card

    # ─── 2. PT 平台位 ───
    pt = analyze_platform(df, dl, config)
    card.pt_result = pt

    # ─── 3. LK 轮廓 ───
    lk = analyze_contour(df, dl, config)
    card.lk_result = lk

    # ─── 4. TY 统一区间 ───
    ty = analyze_squeeze(df, dl, config)
    card.ty_result = ty

    # ─── 5. DN 动能 ───
    dn = analyze_momentum(df, dl, pt, ty, config)
    card.dn_result = dn

    # DN方向确定后，激活对应平台位
    if not dn.pending and dn.direction:
        activate_platform(pt, dn.direction)

    # 更新TY的gap（使用实际触发K线位置而非结构末端）
    if ty.passed and not dn.pending and dn.trigger_idx >= 0:
        actual_gap = dn.trigger_idx - ty.squeeze_end_idx
        ty.gap_to_trigger = max(0, actual_gap)

    # ─── 6. SF 释放级别 ───
    sf = analyze_release(df, dl, dn, config)
    card.sf_result = sf

    # ─── 汇总 ───
    _finalize_card(card, config)
    return card


def _finalize_card(card: ScoreCard, config: AnalyzerConfig):
    """汇总各维度结果，生成综合评级和操作建议。"""

    if card.early_terminated:
        card.overall_passed = False
        card.overall_grade = "不合格"
        card.action_recommendation = card.early_terminate_reason
        _build_conclusions(card)
        return

    # 一票否决检查
    vetoed = False

    if card.pt_result and card.pt_result.score == GradeScore.C:
        card.disqualify_reasons.append("PT(平台位) = C，未找到有效关键位")
        vetoed = True

    if card.lk_result and card.lk_result.score == GradeScore.C:
        card.disqualify_reasons.append("LK(轮廓) = C，平整度/均匀性不足")
        vetoed = True

    if card.ty_result and card.ty_result.score == GradeScore.C:
        card.disqualify_reasons.append("TY(统一区间) = C，尾部未形成有效压缩")
        vetoed = True

    if card.dn_result and card.dn_result.score == GradeScore.C and not card.dn_result.pending:
        card.disqualify_reasons.append("DN(动能) = C，突破力度不足")
        vetoed = True

    if card.sf_result and card.sf_result.score == ReleaseLevel.THIRD:
        card.disqualify_reasons.append("SF(释放级别) = 3rd，前置释放过大")
        vetoed = True

    if vetoed:
        card.overall_passed = False
        card.weighted_score = 0
        card.overall_grade = "不合格（一票否决）"
        card.action_recommendation = "不建议开仓: " + "; ".join(card.disqualify_reasons)
        _build_conclusions(card)
        return

    # 加权评分
    score_map = {GradeScore.S: 4, GradeScore.A: 3, GradeScore.B: 2, GradeScore.C: 1}

    pt_val = score_map.get(card.pt_result.score, 0) if card.pt_result else 0
    lk_val = score_map.get(card.lk_result.score, 0) if card.lk_result else 0
    ty_val = score_map.get(card.ty_result.score, 0) if card.ty_result else 0

    # DN pending 时不参与加权计算，权重分给其他维度
    if card.dn_result and not card.dn_result.pending:
        dn_val = score_map.get(card.dn_result.score, 0)
        weighted = (pt_val * config.weight_pt +
                    dn_val * config.weight_dn +
                    ty_val * config.weight_ty +
                    lk_val * config.weight_lk)
    else:
        # DN 未触发，重新分配权重（去掉DN权重，其余等比放大）
        total_w = config.weight_pt + config.weight_ty + config.weight_lk
        if total_w > 0:
            weighted = (pt_val * config.weight_pt / total_w +
                        ty_val * config.weight_ty / total_w +
                        lk_val * config.weight_lk / total_w)
        else:
            weighted = 0

    card.weighted_score = round(weighted, 2)

    # 综合评级
    if weighted >= config.grade_excellent:
        card.overall_grade = "优秀开仓机会"
        card.overall_passed = True
    elif weighted >= config.grade_qualified:
        card.overall_grade = "合格开仓机会"
        card.overall_passed = True
    elif weighted >= config.grade_marginal:
        card.overall_grade = "勉强合格，需谨慎"
        card.overall_passed = True
    else:
        card.overall_grade = "不建议开仓"
        card.overall_passed = False

    # 操作建议
    if card.dn_result and card.dn_result.pending:
        card.action_recommendation = (
            f"结构已形成（{card.overall_grade}），等待突破K线出现后确认动能"
        )
    elif card.sf_result:
        if card.sf_result.score == ReleaseLevel.FIRST and card.overall_passed:
            card.action_recommendation = "满足开仓条件，可直接执行"
        elif card.sf_result.score == ReleaseLevel.SECOND and card.overall_passed:
            card.action_recommendation = "需等待回踩平台位后执行"
        else:
            card.action_recommendation = card.sf_result.action_advice
    else:
        card.action_recommendation = card.overall_grade

    # 生成精简结论
    _build_conclusions(card)


def _build_conclusions(card: ScoreCard):
    """
    生成精简结论行。

    格式: 看多：DLS/PTA/LKB/TYS/DNS/1st  结论：xxx  原因：xxx
    DN确定方向后：主结论(确定方向) + 备注(另一侧参考)
    DN pending时：两侧分别输出
    """
    card.conclusion_lines = []

    # DL 不通过 → 单行
    if card.early_terminated:
        card.conclusion_lines.append("DLF — 不满足独立结构条件")
        return

    # ─── 提取各维度分数标签 ───
    dl_tag = "S" if card.dl_result and card.dl_result.passed else "F"
    lk_tag = str(card.lk_result.score) if card.lk_result else "?"
    ty_tag = str(card.ty_result.score) if card.ty_result else "?"

    dn = card.dn_result
    dn_pending = dn and dn.pending
    dn_tag = "C" if (dn_pending or not dn) else str(dn.score)

    sf_tag = str(card.sf_result.score) if card.sf_result else "--"

    pt = card.pt_result
    has_resistance = (pt and pt.resistance_price > 0
                      and pt.resistance_score.value >= GradeScore.B.value)
    has_support = (pt and pt.support_price > 0
                   and pt.support_score.value >= GradeScore.B.value)

    # ─── 确定要输出的方向 ───
    # A股只能做多，不生成看空结论
    long_only = (card.market == 'cn')

    # (direction_type, is_main)  is_main=True 为主结论, False 为备注
    directions = []

    if dn and not dn.pending and dn.direction:
        # DN 已确定方向 → 主结论为确定方向，另一侧为备注
        if dn.direction == 'bullish':
            directions.append(('bullish', True))
            if has_support and not long_only:
                directions.append(('bearish', False))
        else:
            if long_only:
                # A股做空方向 → 降级为"不建议"而非"看空"
                directions.append(('bearish_reject', True))
            else:
                directions.append(('bearish', True))
                if has_resistance:
                    directions.append(('bullish', False))
    else:
        # DN pending → 有效的每侧都输出主结论
        if has_resistance:
            directions.append(('bullish', True))
        if has_support and not long_only:
            directions.append(('bearish', True))

    if not directions:
        # 无有效方向
        pt_tag = str(pt.score) if pt else "?"
        score_str = f"DL{dl_tag}/PT{pt_tag}/LK{lk_tag}/TY{ty_tag}/DN{dn_tag}/{sf_tag}"
        if card.disqualify_reasons:
            conclusion = "不建议"
            reason = "; ".join(r.split("，")[0] for r in card.disqualify_reasons)
        elif long_only and has_support and not has_resistance:
            conclusion = "等突破"
            reason = "仅有支撑位，等待阻力位形成"
        else:
            conclusion = "无有效平台位"
            reason = ""
        dir_label = "待定(多)" if (long_only and has_support) else "待定"
        line = f"{dir_label}：{score_str}  结论：{conclusion}"
        if reason:
            line += f"  原因：{reason}"
        card.conclusion_lines.append(line)
        return

    # ─── 为每个方向生成结论行 ───
    for dir_type, is_main in directions:
        # A股做空方向 → 直接输出不建议
        if dir_type == 'bearish_reject':
            pt_tag = str(pt.support_score) if pt and pt.support_price > 0 else "?"
            score_str = f"DL{dl_tag}/PT{pt_tag}/LK{lk_tag}/TY{ty_tag}/DN{dn_tag}/{sf_tag}"
            card.conclusion_lines.append(
                f"待定(空)：{score_str}  结论：不建议  原因：A股不做空"
            )
            continue

        if dir_type == 'bullish':
            dir_label = "待定(多)" if dn_pending else "看多"
            pt_score = pt.resistance_score if pt and pt.resistance_price > 0 else GradeScore.C
        else:
            dir_label = "待定(空)" if dn_pending else "看空"
            pt_score = pt.support_score if pt and pt.support_price > 0 else GradeScore.C

        pt_tag = str(pt_score)
        score_str = f"DL{dl_tag}/PT{pt_tag}/LK{lk_tag}/TY{ty_tag}/DN{dn_tag}/{sf_tag}"

        # 结论文本 + 原因
        if card.disqualify_reasons:
            conclusion = "不建议"
            reason = "; ".join(
                r.split("，")[0] for r in card.disqualify_reasons
            )
        elif card.overall_passed:
            if card.sf_result and card.sf_result.score == ReleaseLevel.FIRST:
                conclusion = "可执行"
            elif card.sf_result and card.sf_result.score == ReleaseLevel.SECOND:
                conclusion = "等回踩"
            elif dn_pending:
                conclusion = "等突破"
            else:
                conclusion = "观望"
            # 正面原因
            parts = []
            if dn and not dn.pending:
                parts.append(f"力度{dn.force_ratio:.1f}x")
                if dn.broke_platform:
                    parts.append("已破平台")
            reason = "、".join(parts)
        else:
            if dn_pending:
                conclusion = "等突破"
                reason = "结构已形成，等待突破确认"
            else:
                conclusion = "不建议"
                reason = "综合评分不足"

        if is_main:
            line = f"{dir_label}：{score_str}  结论：{conclusion}"
            if reason:
                line += f"  原因：{reason}"
        else:
            # 备注行
            if dir_type == 'bullish' and pt:
                zone_info = (f"阻力区间 {pt.resistance_zone_low:.2f}"
                             f"~{pt.resistance_zone_high:.2f}")
            elif pt:
                zone_info = (f"支撑区间 {pt.support_zone_low:.2f}"
                             f"~{pt.support_zone_high:.2f}")
            else:
                zone_info = ""
            line = f"备注：{score_str}  {zone_info} 参考"

        card.conclusion_lines.append(line)
