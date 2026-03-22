"""
终端报告输出

美观的终端格式化输出，每个维度显示评分、关键数值、推理说明。
"""
import sys

from core.types import (
    ScoreCard, GradeScore, ReleaseLevel
)


# ─── ANSI 颜色支持 ───

def _supports_color() -> bool:
    """检测终端是否支持颜色"""
    if sys.platform == 'win32':
        try:
            import os
            # Windows Terminal / VS Code 等现代终端支持 ANSI
            if os.environ.get('WT_SESSION') or os.environ.get('TERM_PROGRAM'):
                return True
            # 尝试启用 Windows 控制台的 ANSI 支持
            import ctypes
            kernel32 = ctypes.windll.kernel32
            # ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
            handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
            mode = ctypes.c_ulong()
            kernel32.GetConsoleMode(handle, ctypes.byref(mode))
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)
            return True
        except Exception:
            return False
    return hasattr(sys.stdout, 'isatty') and sys.stdout.isatty()


USE_COLOR = _supports_color()


def _c(text: str, color: str) -> str:
    """终端着色"""
    if not USE_COLOR:
        return text
    colors = {
        'green': '\033[92m',
        'yellow': '\033[93m',
        'red': '\033[91m',
        'cyan': '\033[96m',
        'bold': '\033[1m',
        'dim': '\033[2m',
        'reset': '\033[0m',
    }
    return f"{colors.get(color, '')}{text}{colors.get('reset', '')}"


def _grade_color(score) -> str:
    """根据评分等级返回颜色名"""
    if isinstance(score, GradeScore):
        return {GradeScore.S: 'green', GradeScore.A: 'green',
                GradeScore.B: 'yellow', GradeScore.C: 'red'}.get(score, 'reset')
    if isinstance(score, ReleaseLevel):
        return {ReleaseLevel.FIRST: 'green', ReleaseLevel.SECOND: 'yellow',
                ReleaseLevel.THIRD: 'red'}.get(score, 'reset')
    return 'reset'


def _bar(score, max_width: int = 24) -> str:
    """生成评分进度条"""
    if isinstance(score, GradeScore):
        ratio = score.value / 4.0
    elif isinstance(score, ReleaseLevel):
        ratio = {ReleaseLevel.FIRST: 1.0, ReleaseLevel.SECOND: 0.6,
                 ReleaseLevel.THIRD: 0.2}.get(score, 0)
    else:
        ratio = 0
    filled = int(ratio * max_width)
    return '#' * filled + '-' * (max_width - filled)


def _format_score_tag(score) -> str:
    """格式化评分标签"""
    color = _grade_color(score)
    if isinstance(score, GradeScore):
        labels = {GradeScore.S: 'S 优秀', GradeScore.A: 'A 良好',
                  GradeScore.B: 'B 合格', GradeScore.C: 'C 不足'}
        label = labels.get(score, str(score))
    elif isinstance(score, ReleaseLevel):
        label = str(score)
    else:
        label = str(score)
    return _c(f"[ {label} ]", color)


# ─── 主输出函数 ───

def print_score_card(card: ScoreCard):
    """输出完整的六维打分报告"""
    w = 64  # 内容宽度

    print()
    print('=' * w)
    print(_c(f"  六维打分分析报告 - {card.symbol}", 'bold'))
    if card.analysis_time:
        print(f"  分析时间: {card.analysis_time.strftime('%Y-%m-%d %H:%M')}")
    if card.data_start and card.data_end:
        print(f"  数据范围: {card.data_start} ~ {card.data_end}")
    print(f"  K线总数: {card.total_klines} 根(小时级)")
    print('=' * w)

    # ─── DL ───
    if card.dl_result:
        dl = card.dl_result
        print()
        print(f"  DL 独立结构  {_bar(dl.score)}  {_format_score_tag(dl.score)}")
        print(f"    K线数: {dl.kline_count}根  |  "
              f"区间: {dl.range_low:.2f}-{dl.range_high:.2f}  |  "
              f"振幅: {dl.range_pct}%")
        for r in dl.reasoning:
            _print_reasoning(r)
        if dl.flaws:
            for f in dl.flaws:
                print(f"    {_c('!', 'yellow')} {f}")

    if card.early_terminated:
        print()
        print('-' * w)
        print(f"  {_c(card.early_terminate_reason, 'red')}")
        _print_footer(card, w)
        return

    # ─── PT ───
    if card.pt_result:
        pt = card.pt_result
        print()
        print(f"  PT 平台位    {_bar(pt.score)}  {_format_score_tag(pt.score)}")
        active_label = "阻力" if pt.platform_type == 'resistance' else "支撑"
        print(f"    激活: {active_label} {pt.platform_price}  |  "
              f"触碰: {pt.touch_count}次  |  "
              f"穿透: {pt.penetration_count}次")
        if pt.resistance_price > 0:
            if pt.resistance_zone_high > 0:
                print(f"    上平台(阻力): {pt.resistance_zone_low:.3f}"
                      f"~{pt.resistance_zone_high:.3f}  "
                      f"({pt.resistance_touch_count}次触碰/"
                      f"影线透{pt.resistance_shadow_penetrations}/"
                      f"实体透{pt.resistance_body_penetrations}, "
                      f"{pt.resistance_score})")
            else:
                print(f"    上平台(阻力): {pt.resistance_price:.3f}  "
                      f"({pt.resistance_touch_count}次触碰/"
                      f"影线透{pt.resistance_shadow_penetrations}/"
                      f"实体透{pt.resistance_body_penetrations}, "
                      f"{pt.resistance_score})")
        if pt.support_price > 0 and card.market != 'cn':
            if pt.support_zone_high > 0:
                print(f"    下平台(支撑): {pt.support_zone_low:.3f}"
                      f"~{pt.support_zone_high:.3f}  "
                      f"({pt.support_touch_count}次触碰/"
                      f"影线透{pt.support_shadow_penetrations}/"
                      f"实体透{pt.support_body_penetrations}, "
                      f"{pt.support_score})")
            else:
                print(f"    下平台(支撑): {pt.support_price:.3f}  "
                      f"({pt.support_touch_count}次触碰/"
                      f"影线透{pt.support_shadow_penetrations}/"
                      f"实体透{pt.support_body_penetrations}, "
                      f"{pt.support_score})")
        for r in pt.reasoning:
            _print_reasoning(r)

    # ─── LK ───
    if card.lk_result:
        lk = card.lk_result
        print()
        if lk.pending:
            print(f"  LK 轮廓      {_bar(GradeScore.C)}  {_c('[ 待定 ]', 'yellow')}")
        else:
            print(f"  LK 轮廓      {_bar(lk.score)}  {_format_score_tag(lk.score)}")
            print(f"    质量分: {lk.quality_score:.2f}  |  "
                  f"振幅CV: {lk.range_cv:.3f}  |  "
                  f"异常K线: {lk.abnormal_count}根({lk.abnormal_ratio:.1%})")
        for r in lk.reasoning:
            _print_reasoning(r)

    # ─── SF ───
    if card.sf_result:
        sf = card.sf_result
        print()
        if sf.pending:
            print(f"  SF 释放级别  {_bar(GradeScore.C)}  {_c('[ 待定 ]', 'yellow')}")
        else:
            print(f"  SF 释放级别  {_bar(sf.score)}  {_format_score_tag(sf.score)}")
            print(f"    尾部偏移: {sf.tail_drift_pct:.2f}%  |  "
                  f"尾长: {sf.tail_length}根  |  "
                  f"方向: {sf.direction or '未定'}")
        for r in sf.reasoning:
            _print_reasoning(r)

    # ─── TY ───
    if card.ty_result:
        ty = card.ty_result
        print()
        if ty.pending:
            print(f"  TY 统一区间  {_bar(GradeScore.C)}  {_c('[ 待定 ]', 'yellow')}")
        else:
            print(f"  TY 统一区间  {_bar(ty.score)}  {_format_score_tag(ty.score)}")
            print(f"    连续缩量: {ty.squeeze_length}根  |  "
                  f"均幅/ATR: {ty.avg_range_ratio:.1%}  |  "
                  f"斜率: {ty.slope_pct:.4f}%")
        for r in ty.reasoning:
            _print_reasoning(r)

    # ─── DN ───
    if card.dn_result:
        dn = card.dn_result
        print()
        if dn.pending:
            print(f"  DN 动能      {_bar(GradeScore.C)}  {_c('[ 待定 ]', 'yellow')}")
            for r in dn.reasoning:
                _print_reasoning(r)
        else:
            print(f"  DN 动能      {_bar(dn.score)}  {_format_score_tag(dn.score)}")
            dir_label = "向上" if dn.direction == 'bullish' else "向下"
            print(f"    方向: {dir_label}  |  "
                  f"力度: {dn.force_ratio:.1f}x  |  "
                  f"合并: {dn.merged_count}根  |  "
                  f"放量: {dn.volume_ratio:.1f}x")
            for r in dn.reasoning:
                _print_reasoning(r)

    _print_footer(card, w)


def _print_reasoning(text: str):
    """输出推理说明行"""
    if text.startswith("警告") or text.startswith("存在瑕疵"):
        print(f"    {_c('!', 'yellow')} {text}")
    else:
        print(f"    > {text}")


def _print_footer(card: ScoreCard, w: int):
    """输出底部精简结论和综合评级"""
    print()
    print('-' * w)

    # 精简结论行
    if card.conclusion_lines:
        for line in card.conclusion_lines:
            if line.startswith("看多") or line.startswith("待定(多)"):
                color = 'green'
            elif line.startswith("看空") or line.startswith("待定(空)"):
                color = 'red'
            elif line.startswith("备注"):
                color = 'dim'
            else:
                color = 'yellow'
            print(f"  {_c(line, color)}")
        print()

    # 仓位建议
    if card.position_size:
        pos_colors = {'1R': 'green', '0.5R': 'yellow', '等待': 'yellow', '不做': 'red'}
        pos_color = pos_colors.get(card.position_size, 'reset')
        print(f"  仓位: {_c(card.position_size, pos_color)}")
        if card.position_reason:
            print(f"    > {card.position_reason}")
        print()

    if card.overall_passed:
        grade_color = 'green'
    elif card.early_terminated:
        grade_color = 'red'
    else:
        grade_color = 'yellow'

    print(f"  综合评级: {_c(card.overall_grade, grade_color)}")

    print('=' * w)
    print()
