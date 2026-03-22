"""
综合评分引擎

编排6个维度的分析流程，六维结果直接展示，不做加权打分和一票否决。
概念顺序: PT → DL → LK → SF → TY → DN（PT反推DL边界）
实现顺序: 粗算DL → PT → 修正DL → LK → SF → TY → DN
"""
import pandas as pd
from datetime import datetime

from core.types import (
    AnalyzerConfig, ScoreCard, GradeScore, ReleaseLevel
)
from core.analyzer.structure import analyze_structure
from core.analyzer.platform import analyze_platform, activate_platform
from core.analyzer.contour import analyze_contour
from core.analyzer.squeeze import analyze_squeeze
from core.analyzer.momentum import analyze_momentum
from core.analyzer.release import analyze_release
from core.utils.helpers import clean_ohlcv


def run_full_analysis(df: pd.DataFrame,
                      symbol: str = "",
                      config: AnalyzerConfig = None,
                      market: str = 'cn') -> ScoreCard:
    """
    运行完整的六维分析流程。

    概念顺序: PT → DL → LK → SF → TY → DN（PT反推DL边界）
    实现顺序: 粗算DL → PT → 修正DL → LK → SF → TY → DN
    A股(market='cn')只检测阻力位，SF方向固定为bullish。
    DL < 90根时继续分析后续维度，但标注"结构未成熟"。
    """
    if config is None:
        config = AnalyzerConfig()

    # 数据清洗
    df = clean_ohlcv(df)

    card = ScoreCard()
    card.symbol = symbol
    card.market = market
    card.analysis_time = datetime.now()
    card.total_klines = len(df)

    if len(df) > 0:
        card.data_start = df.index[0]
        card.data_end = df.index[-1]

    # ─── 1. DL 独立结构 ───
    dl = analyze_structure(df, config)
    card.dl_result = dl

    # DL < 90根时不终止，继续分析但标注
    if dl.kline_count == 0:
        # 完全没有检测到结构
        card.early_terminated = True
        card.early_terminate_reason = "未检测到盘整结构，后续维度无法分析"
        _finalize_card(card)
        return card

    # ─── 2. PT 平台位 ───
    pt = analyze_platform(df, dl, config, market=market)
    card.pt_result = pt

    # ─── 2b. PT→DL边界修正 ───
    # 如果PT的第一个触碰点远在DL起点之后，收紧DL边界
    _refine_dl_from_pt(dl, pt)

    # ─── 3. LK 轮廓 ───
    lk = analyze_contour(df, dl, config)
    card.lk_result = lk

    # ─── 4. SF 释放级别（在TY/DN之前，评估尾部是否向突破方向蹭） ───
    # A股方向固定为bullish，美股从PT推断
    if market == 'cn':
        sf_direction = 'bullish'
    else:
        sf_direction = _determine_direction_from_pt(pt)
    sf = analyze_release(df, dl, config, direction=sf_direction, platform=pt)
    card.sf_result = sf

    # ─── 5. TY 统一区间 ───
    ty = analyze_squeeze(df, dl, config, platform=pt)
    card.ty_result = ty

    # ─── 6. DN 动能 ───
    dn = analyze_momentum(df, dl, pt, ty, config)
    card.dn_result = dn

    # DN方向确定后，激活对应平台位
    if not dn.pending and dn.direction:
        activate_platform(pt, dn.direction)

    # ─── 7. DL参考信息：记录急跌/急涨和倾斜（不影响DL评分） ───
    _note_dl_context(card, config)

    # ─── 8. PT最后一次测试后的调整检查 ───
    _check_last_test_adjustment(card, config)

    # 更新TY的gap（使用实际触发K线位置而非结构末端）
    if ty.passed and not dn.pending and dn.trigger_idx >= 0:
        actual_gap = dn.trigger_idx - ty.squeeze_end_idx
        ty.gap_to_trigger = max(0, actual_gap)

    # ─── 汇总 ───
    _finalize_card(card)
    return card


def _determine_direction_from_pt(pt) -> str:
    """
    从PT结果推断方向。
    优先看多：仅当只有支撑位达标（无有效阻力位）时才看空，其余情况一律看多。
    """
    if not pt:
        return 'bullish'
    has_resistance = (pt.resistance_price > 0
                      and pt.resistance_score.value >= GradeScore.B.value)
    has_support = (pt.support_price > 0
                   and pt.support_score.value >= GradeScore.B.value)
    if has_support and not has_resistance:
        return 'bearish'
    return 'bullish'


def _refine_dl_from_pt(dl, pt):
    """
    PT→DL边界修正：用PT的触碰点范围收紧DL边界。

    如果PT第一个触碰点远在DL起点之后，说明DL结构扩展过大，
    需要收紧到PT实际活跃的范围。
    """
    if not dl or not pt or dl.kline_count == 0:
        return

    # 收集所有有效触碰点的本地索引
    all_touches = []
    if pt.resistance_touches:
        all_touches.extend([t[0] for t in pt.resistance_touches])
    if pt.support_touches:
        all_touches.extend([t[0] for t in pt.support_touches])

    if len(all_touches) < 2:
        return

    first_touch_local = min(all_touches)
    # 在第一个触碰点之前留一些缓冲（20根K线），但不超过原始起点
    buffer = 20
    new_start_local = max(0, first_touch_local - buffer)
    new_start_global = dl.structure_start_idx + new_start_local

    # 只在新起点显著靠后时才修正（至少缩减20%）
    original_length = dl.kline_count
    new_length = dl.structure_end_idx - new_start_global + 1
    if new_length < original_length * 0.8 and new_length >= 10:
        old_start = dl.structure_start_idx
        dl.structure_start_idx = new_start_global
        dl.kline_count = new_length
        # 重新判断是否通过
        dl.passed = dl.kline_count >= 90
        if dl.kline_count >= 90:
            dl.score = GradeScore.S
        else:
            dl.score = GradeScore.C
        dl.reasoning.append(
            f"PT反推DL边界: 原始{original_length}K→修正{new_length}K"
            f"（第一个PT触碰点在原结构第{first_touch_local}根）"
        )


def _note_dl_context(card: ScoreCard, config: AnalyzerConfig):
    """
    记录DL的急跌/急涨和倾斜信息作为参考（不改变DL评分）。

    DL评分只看K线数量：≥90→S，<90→C。
    急跌/倾斜等信息仅作为reasoning备注，供人工参考。
    """
    dl = card.dl_result
    if not dl or dl.kline_count == 0:
        return

    steep = config.dl_steep_decline_threshold  # 0.50
    tilt = config.dl_tilt_threshold            # 0.08

    if dl.prior_trend_slope < -steep:
        dl.reasoning.append(
            f"参考: 结构前有急跌（斜率 {dl.prior_trend_slope:.3f}%/K）"
        )
    elif dl.prior_trend_slope > steep:
        dl.reasoning.append(
            f"参考: 结构前有急涨（斜率 {dl.prior_trend_slope:.3f}%/K）"
        )

    if dl.structure_slope > tilt:
        dl.reasoning.append(
            f"参考: 结构向右上倾斜（{dl.structure_slope:.3f}%/K）"
        )
    elif dl.structure_slope < -tilt:
        dl.reasoning.append(
            f"参考: 结构向右下倾斜（{dl.structure_slope:.3f}%/K）"
        )


def _check_last_test_adjustment(card: ScoreCard, config: AnalyzerConfig):
    """
    检查PT最后一次测试后是否有调整结构再进入TY+DN。

    最后一次测试完不能直接突破，中间需要走出一段调整。
    """
    pt = card.pt_result
    dl = card.dl_result
    dn = card.dn_result
    ty = card.ty_result

    if not pt or not dl or dl.kline_count == 0:
        return

    # 获取激活平台的最后一次测试索引（本地索引）
    touches = pt.touch_points
    if not touches:
        return

    last_touch_local_idx = touches[-1][0]
    last_touch_global_idx = dl.structure_start_idx + last_touch_local_idx

    # 找到TY或DN中较早的起点
    earliest_event = None
    if ty and ty.squeeze_length > 0:
        earliest_event = ty.squeeze_start_idx
    if dn and not dn.pending and dn.trigger_idx >= 0:
        if earliest_event is None or dn.trigger_idx < earliest_event:
            earliest_event = dn.trigger_idx

    if earliest_event is None:
        return

    gap = earliest_event - last_touch_global_idx
    min_gap = config.pt_adjustment_min_bars  # 8

    if gap < min_gap:
        pt.reasoning.append(
            f"警告: 最后一次测试(idx={last_touch_global_idx})与"
            f"TY/DN(idx={earliest_event})仅间隔{gap}根K线（<{min_gap}），"
            f"缺乏充分调整"
        )


def _finalize_card(card: ScoreCard):
    """汇总各维度结果，生成结论行。不做加权打分和一票否决。"""

    if card.early_terminated:
        card.overall_grade = "无法分析"
        card.action_recommendation = card.early_terminate_reason
        card.position_size = "不做"
        card.position_reason = card.early_terminate_reason
        _build_conclusions(card)
        return

    # 直接根据各维度状态生成建议（仅供参考，不做强制判断）
    if card.dl_result and not card.dl_result.passed:
        card.overall_grade = f"结构未成熟({card.dl_result.kline_count}根)"
        card.action_recommendation = "调整不到位，可继续观察"
    elif card.dn_result and card.dn_result.pending:
        card.overall_grade = "等待突破"
        card.action_recommendation = "结构已形成，等待突破K线出现"
    else:
        card.overall_grade = "六维分析完成"
        card.action_recommendation = ""

    # 仓位评估
    card.position_size, card.position_reason = _determine_position(card)

    # 生成结论行
    _build_conclusions(card)


def _determine_position(card: ScoreCard):
    """
    根据六维结果确定仓位档位。

    规则:
    - DN 待定 → 等待
    - SF=3rd → 不做（动能已消耗完）
    - 所有维度均≥A（SF=1st视为≥A） → 1R
    - TY≥A + DN≥S → 0.5R（即使PT/LK/SF不够好）
    - SF=2nd + 其他一般 → 等待
    - 其余 → 不做
    """
    # DN pending → 等待
    if card.dn_result and card.dn_result.pending:
        return "等待", "DN尚未触发，等待突破K线出现"

    # SF=3rd → 不做
    if card.sf_result and card.sf_result.score == ReleaseLevel.THIRD:
        return "不做", f"SF=3rd，动能已消耗完，需等待全新独立结构"

    # DL未通过 → 等待观察
    if card.dl_result and not card.dl_result.passed:
        return "等待", f"DL未成熟({card.dl_result.kline_count}根)，继续观察"

    # TY=pending → 等待
    if card.ty_result and card.ty_result.pending:
        return "等待", "TY尚未形成，挤压不足，继续观察"

    # 收集除DL外各维度不达A的原因（DL只有S/C，已在上面处理）
    shortfalls = []

    pt = card.pt_result
    if pt:
        # 用激活平台的评分（PT整体score在DN激活后更新）
        if pt.score.value < GradeScore.A.value:
            shortfalls.append(f"PT={pt.score}")
    else:
        shortfalls.append("PT=?")

    lk = card.lk_result
    if not lk or lk.score.value < GradeScore.A.value:
        shortfalls.append(f"LK={lk.score}" if lk else "LK=?")

    sf = card.sf_result
    sf_is_1st = sf and sf.score == ReleaseLevel.FIRST
    if not sf_is_1st:
        shortfalls.append(f"SF={sf.score}" if sf else "SF=?")

    ty = card.ty_result
    if not ty or ty.pending or ty.score.value < GradeScore.A.value:
        shortfalls.append(f"TY={'待定' if (ty and ty.pending) else (ty.score if ty else '?')}")

    dn = card.dn_result
    if not dn or dn.score.value < GradeScore.A.value:
        shortfalls.append(f"DN={dn.score}" if dn else "DN=?")

    # 1R: 所有维度均≥A（SF=1st算≥A）
    if not shortfalls:
        return "1R", "所有维度均≥A，标准仓位"

    # 0.5R: TY≥A + DN≥S，即使PT/LK/SF不够好也可半仓
    ty_good = ty and ty.score.value >= GradeScore.A.value
    dn_great = dn and dn.score == GradeScore.S
    if ty_good and dn_great:
        return "0.5R", f"TY≥A+DN=S，可半仓做（{', '.join(shortfalls)}）"

    # SF=2nd + 其他维度一般 → 等待
    if sf and sf.score == ReleaseLevel.SECOND:
        return "等待", f"SF=2nd，需再等一段调整（{', '.join(shortfalls)}）"

    # 其余情况 → 不做
    return "不做", f"条件不足（{', '.join(shortfalls)}）"


def _build_conclusions(card: ScoreCard):
    """
    生成精简结论行。

    格式: {方向}：DL{等级}/PT{等级}(原因)/LK{等级}(原因)/SF{级别}(原因)/TY{等级}(原因)/DN{等级}(原因)
    非S/非1st评分后面括号内附带简短原因。
    """
    card.conclusion_lines = []

    # 完全无结构 → 单行
    if card.early_terminated:
        card.conclusion_lines.append("DL? — 未检测到盘整结构")
        return

    # ─── 提取各维度分数标签（含原因） ───
    dl = card.dl_result
    if dl:
        if dl.score == GradeScore.S:
            dl_tag = "S"
        elif dl.kline_count > 0:
            dl_tag = f"C({dl.kline_count}根)"
        else:
            dl_tag = "F"
    else:
        dl_tag = "F"

    lk = card.lk_result
    lk_tag = _tag_with_reason(lk.score, _get_lk_reason(lk)) if lk else "?"

    sf = card.sf_result
    sf_tag = _tag_sf_with_reason(sf) if sf else "--"

    ty = card.ty_result
    if ty and ty.pending:
        ty_tag = "待定"
    elif ty:
        ty_tag = _tag_with_reason(ty.score, _get_ty_reason(ty))
    else:
        ty_tag = "?"

    dn = card.dn_result
    dn_pending = dn and dn.pending
    if dn_pending:
        dn_tag = "待定"
    elif dn:
        dn_tag = _tag_with_reason(dn.score, _get_dn_reason(dn))
    else:
        dn_tag = "?"

    pt = card.pt_result
    has_resistance = (pt and pt.resistance_price > 0
                      and pt.resistance_score.value >= GradeScore.B.value)
    has_support = (pt and pt.support_price > 0
                   and pt.support_score.value >= GradeScore.B.value)

    # ─── 确定要输出的方向 ───
    long_only = (card.market == 'cn')

    directions = []

    if dn and not dn.pending and dn.direction:
        if dn.direction == 'bullish':
            directions.append(('bullish', True))
            if has_support and not long_only:
                directions.append(('bearish', False))
        else:
            if long_only:
                directions.append(('bearish_reject', True))
            else:
                directions.append(('bearish', True))
                if has_resistance:
                    directions.append(('bullish', False))
    else:
        if has_resistance:
            directions.append(('bullish', True))
        if has_support and not long_only:
            directions.append(('bearish', True))

    if not directions:
        pt_tag = _tag_with_reason(pt.score, _get_pt_reason(pt, pt.score)) if pt else "?"
        score_str = f"DL{dl_tag} / PT{pt_tag} / LK{lk_tag} / {sf_tag} / TY{ty_tag} / DN{dn_tag}"
        simple_str = f"DL{_strip_reason(dl_tag)} / PT{_strip_reason(pt_tag)} / LK{_strip_reason(lk_tag)} / {_strip_reason(sf_tag)} / TY{_strip_reason(ty_tag)} / DN{_strip_reason(dn_tag)}"
        dir_label = "待定"
        card.conclusion_lines.append(f"{dir_label}：{simple_str}")
        return

    # ─── 为每个方向生成结论行 ───
    for dir_type, is_main in directions:
        if dir_type == 'bearish_reject':
            pt_s = pt.support_score if pt and pt.support_price > 0 else GradeScore.C
            pt_tag = _tag_with_reason(pt_s, _get_pt_reason(pt, pt_s, 'support'))
            score_str = f"DL{dl_tag} / PT{pt_tag} / LK{lk_tag} / {sf_tag} / TY{ty_tag} / DN{dn_tag}"
            simple_str = f"DL{_strip_reason(dl_tag)} / PT{_strip_reason(pt_tag)} / LK{_strip_reason(lk_tag)} / {_strip_reason(sf_tag)} / TY{_strip_reason(ty_tag)} / DN{_strip_reason(dn_tag)}"
            card.conclusion_lines.append(f"看空：{simple_str}  (A股不做空)")
            continue

        if dir_type == 'bullish':
            dir_label = "待定(多)" if dn_pending else "看多"
            pt_score = pt.resistance_score if pt and pt.resistance_price > 0 else GradeScore.C
            pt_tag = _tag_with_reason(pt_score, _get_pt_reason(pt, pt_score, 'resistance'))
        else:
            dir_label = "待定(空)" if dn_pending else "看空"
            pt_score = pt.support_score if pt and pt.support_price > 0 else GradeScore.C
            pt_tag = _tag_with_reason(pt_score, _get_pt_reason(pt, pt_score, 'support'))

        score_str = f"DL{dl_tag} / PT{pt_tag} / LK{lk_tag} / {sf_tag} / TY{ty_tag} / DN{dn_tag}"
        simple_str = f"DL{_strip_reason(dl_tag)} / PT{_strip_reason(pt_tag)} / LK{_strip_reason(lk_tag)} / {_strip_reason(sf_tag)} / TY{_strip_reason(ty_tag)} / DN{_strip_reason(dn_tag)}"

        if is_main:
            card.conclusion_lines.append(f"{dir_label}：{simple_str}")
        else:
            if dir_type == 'bullish' and pt:
                zone_info = (f"阻力 {pt.resistance_zone_low:.2f}"
                             f"~{pt.resistance_zone_high:.2f}")
            elif pt:
                zone_info = (f"支撑 {pt.support_zone_low:.2f}"
                             f"~{pt.support_zone_high:.2f}")
            else:
                zone_info = ""
            card.conclusion_lines.append(f"备注：{simple_str}  {zone_info}")


# ─── 各维度简短原因提取 ───

def _tag_with_reason(score: GradeScore, reason: str) -> str:
    """S级不带原因，其他等级带括号原因。"""
    tag = str(score)
    if score == GradeScore.S or not reason:
        return tag
    return f"{tag}({reason})"


def _strip_reason(tag: str) -> str:
    """去掉标签中括号内的原因，只保留等级。"""
    idx = tag.find('(')
    return tag[:idx] if idx >= 0 else tag


def _tag_sf_with_reason(sf) -> str:
    """SF用1st/2nd/3rd表示，非1st带原因。"""
    tag = str(sf.score)
    if sf.score == ReleaseLevel.FIRST:
        return tag
    reason = _get_sf_reason(sf)
    if reason:
        return f"{tag}({reason})"
    return tag


def _get_pt_reason(pt, pt_score: GradeScore, side: str = '') -> str:
    """PT非S评分的简短原因。"""
    if not pt or pt_score == GradeScore.S:
        return ""

    # 判断使用哪一侧的数据
    if side == 'resistance' or (not side and pt.resistance_price > 0):
        touches = pt.resistance_touch_count
        body_pens = pt.resistance_body_penetrations
        shadow_pens = pt.resistance_shadow_penetrations
        post_tests = pt.resistance_post_pen_tests
    elif side == 'support' or (not side and pt.support_price > 0):
        touches = pt.support_touch_count
        body_pens = pt.support_body_penetrations
        shadow_pens = pt.support_shadow_penetrations
        post_tests = pt.support_post_pen_tests
    else:
        touches = pt.touch_count
        body_pens = pt.penetration_count
        shadow_pens = 0
        post_tests = 0

    if touches < 3:
        return f"触碰{touches}次不足3次"
    if body_pens > 0 and post_tests < 2:
        return f"实体穿越{body_pens}次恢复不够"
    if body_pens > 0 and post_tests >= 2:
        return f"实体穿越后{post_tests}次恢复"
    if shadow_pens > 0:
        return f"影线穿越{shadow_pens}次"
    return "间隔不足"


def _get_lk_reason(lk) -> str:
    """LK非S评分的简短原因。"""
    if not lk or lk.score == GradeScore.S:
        return ""
    parts = []
    if lk.tail_break:
        parts.append("尾部破位")
    if lk.density_score < 0.4:
        parts.append("中间松散")
    if lk.symmetry_score < 0.3:
        parts.append("对称性差")
    if not parts:
        if lk.quality_score < 0.4:
            parts.append("形态杂乱")
        else:
            parts.append("轮廓瑕疵")
    return "/".join(parts[:2])


def _get_sf_reason(sf) -> str:
    """SF非1st评分的简短原因。"""
    if not sf or sf.score == ReleaseLevel.FIRST:
        return ""
    if sf.score == ReleaseLevel.SECOND:
        return f"尾部偏移{sf.tail_drift_pct:.1f}%"
    return f"尾部蹭幅大{sf.tail_drift_pct:.1f}%"


def _get_ty_reason(ty) -> str:
    """TY非S评分的简短原因。"""
    if not ty or ty.score == GradeScore.S:
        return ""
    if ty.squeeze_length < 3:
        return "小K线不足"
    if ty.slope_pct > 0.02:
        return f"斜率{ty.slope_pct:.2f}%偏大"
    if ty.squeeze_length < 4:
        return f"仅{ty.squeeze_length}根小K线"
    return "挤压不充分"


def _get_dn_reason(dn) -> str:
    """DN非S评分的简短原因。"""
    if not dn or dn.score == GradeScore.S:
        return ""
    if dn.pending:
        return "等待突破"
    parts = []
    if dn.merged_count > 1:
        parts.append(f"合并{dn.merged_count}根")
    if not dn.broke_platform:
        parts.append("未穿越平台")
    if dn.force_ratio < 1.5:
        parts.append("力度不足")
    if not parts:
        parts.append("突破质量一般")
    return "/".join(parts[:2])
