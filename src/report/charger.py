"""
图表生成模块

将六维分析结果叠加在K线蜡烛图上，生成 PNG 图表。
通过 --chart 开关控制是否生成。
"""
import os
from datetime import datetime
from pathlib import Path

import matplotlib
matplotlib.use('Agg')  # 无头模式，不弹窗

import matplotlib.pyplot as plt
import matplotlib.patches as patches
from matplotlib.font_manager import FontProperties
from matplotlib.gridspec import GridSpec
import mplfinance as mpf
import pandas as pd
import numpy as np

from src.analyzer.base import (
    ScoreCard, GradeScore, PassFail, ReleaseLevel
)


# ─── 中文字体 ───

def _setup_font() -> FontProperties:
    """查找并返回中文字体 FontProperties"""
    candidates = [
        'C:/Windows/Fonts/msyh.ttc',
        'C:/Windows/Fonts/simhei.ttf',
    ]
    for path in candidates:
        if os.path.exists(path):
            return FontProperties(fname=path, size=9)
    # fallback
    return FontProperties(size=9)


_FONT = _setup_font()
_FONT_TITLE = _setup_font()
_FONT_TITLE._size = 12

# 全局 rcParams 兜底
matplotlib.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'sans-serif']
matplotlib.rcParams['axes.unicode_minus'] = False


# ─── 颜色方案 ───

_COLORS = {
    'dl': '#4A90D9',
    'pt': '#E74C3C',
    'ty': '#F39C12',
    'dn_bull': '#C0392B',
    'dn_bear': '#27AE60',
    'sf_1st': '#27AE60',
    'sf_2nd': '#F39C12',
    'sf_3rd': '#E74C3C',
    'fail': '#999999',
}


def _score_color(score) -> str:
    """评分等级 → 颜色"""
    if isinstance(score, PassFail):
        return '#27AE60' if score == PassFail.S else '#E74C3C'
    if isinstance(score, GradeScore):
        return {
            GradeScore.S: '#27AE60',
            GradeScore.A: '#2ECC71',
            GradeScore.B: '#F39C12',
            GradeScore.C: '#E74C3C',
        }.get(score, '#999999')
    if isinstance(score, ReleaseLevel):
        return {
            ReleaseLevel.FIRST: '#27AE60',
            ReleaseLevel.SECOND: '#F39C12',
            ReleaseLevel.THIRD: '#E74C3C',
        }.get(score, '#999999')
    return '#999999'


# ─── 主入口 ───

def generate_chart(df: pd.DataFrame, card: ScoreCard,
                   output_dir: str = "charts") -> str:
    """
    生成六维分析图表并保存为 PNG。

    Args:
        df: 清洗后的 OHLCV DataFrame (DatetimeIndex)
        card: 六维分析 ScoreCard
        output_dir: 输出目录

    Returns:
        生成的 PNG 文件路径
    """
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f"{card.symbol}_{ts}.png"
    filepath = str(out_path / filename)

    fig = _build_chart(df, card)
    fig.savefig(filepath, dpi=150, bbox_inches='tight', facecolor='white')
    plt.close(fig)

    return filepath


# ─── 图表构建 ───

def _build_chart(df: pd.DataFrame, card: ScoreCard) -> plt.Figure:
    """构建完整的六维分析图表"""

    fig = plt.figure(figsize=(36, 10))
    gs = GridSpec(2, 1, height_ratios=[7, 1.5], hspace=0.05, figure=fig)
    fig.subplots_adjust(left=0.03, right=0.95, bottom=0.10)  # 压缩左边距，右侧留给PT标签

    ax_main = fig.add_subplot(gs[0])
    ax_vol = fig.add_subplot(gs[1], sharex=ax_main)

    # K线样式: 红涨绿跌 (A股习惯)
    mc = mpf.make_marketcolors(
        up='#E74C3C', down='#27AE60',
        edge='inherit', wick='inherit',
        volume={'up': '#E74C3C', 'down': '#27AE60'}
    )
    style = mpf.make_mpf_style(marketcolors=mc, gridstyle=':', y_on_right=True)

    # 绘制K线
    mpf.plot(df, type='candle', style=style,
             ax=ax_main, volume=ax_vol,
             datetime_format='%m-%d',
             xrotation=0, show_nontrading=False)

    # 收紧x轴: 左侧贴近K线起点，右侧留出空间给PT标签
    n = len(df)
    right_pad = max(int(n * 0.06), 15)  # 右侧留6%或至少15根位置给标签
    ax_main.set_xlim(-2, n + right_pad)

    # 标题: 代码 + 股票名称
    name_part = card.symbol_name if card.symbol_name else card.symbol
    title = f"{card.symbol} {name_part}"
    if card.data_start and card.data_end:
        s = pd.Timestamp(card.data_start).strftime('%Y-%m-%d')
        e = pd.Timestamp(card.data_end).strftime('%Y-%m-%d')
        title += f"  ({s} ~ {e})"
    ax_main.set_title(title, fontproperties=_FONT_TITLE, pad=10)

    # 叠加标注: 只绘制 DL 和 PT
    if card.dl_result:
        _draw_dl(ax_main, card.dl_result, df)

    if card.pt_result and not card.early_terminated:
        _draw_pt(ax_main, card.pt_result, card.dl_result)

    # 底部精简结论 (用 fig.text 直接写在图表底部边距)
    _draw_summary(fig, card)

    # 隐藏成交量面板的x轴标签 (与主面板共享)
    plt.setp(ax_main.get_xticklabels(), visible=False)

    return fig


# ─── DL 独立结构 ───

def _draw_dl(ax, dl, df):
    """绘制 DL 结构尺标: 在K线下方用水平括号标注结构区间范围"""
    if dl.kline_count == 0:
        return

    start = dl.structure_start_idx
    end = dl.structure_end_idx
    color = _COLORS['dl'] if dl.passed else _COLORS['fail']

    # 计算尺标y位置: 在K线最低价下方留出空间
    y_low, y_high = ax.get_ylim()
    y_range = y_high - y_low
    ruler_y = y_low + y_range * 0.02          # 尺标主线
    tick_h = y_range * 0.015                   # 竖线高度

    # 水平主线
    ax.plot([start, end], [ruler_y, ruler_y],
            color=color, linewidth=1.5, alpha=0.8, zorder=8,
            solid_capstyle='butt')

    # 两端竖线
    ax.plot([start, start], [ruler_y, ruler_y + tick_h],
            color=color, linewidth=1.5, alpha=0.8, zorder=8)
    ax.plot([end, end], [ruler_y, ruler_y + tick_h],
            color=color, linewidth=1.5, alpha=0.8, zorder=8)

    # 中间标签
    mid_x = (start + end) / 2
    label = f"DL {dl.kline_count}K ({dl.score})"
    ax.text(mid_x, ruler_y - y_range * 0.008, label,
            fontproperties=_FONT, color=color,
            fontsize=8, ha='center', va='top', zorder=10)


# ─── PT 平台位 ───

_COLORS_PT_RES = '#E74C3C'  # 阻力位红色
_COLORS_PT_SUP = '#2980B9'  # 支撑位蓝色


def _draw_pt(ax, pt, dl):
    """绘制 PT 上下平台区间: 阻力区间(红) + 支撑区间(蓝) + 触碰点"""
    if not dl or dl.kline_count == 0:
        return

    start = dl.structure_start_idx
    end = dl.structure_end_idx

    # 绘制阻力区间（上平台）
    if pt.resistance_price > 0:
        _draw_one_platform(ax, start, end,
                           pt.resistance_price,
                           pt.resistance_zone_high,
                           pt.resistance_zone_low,
                           pt.resistance_touches,
                           pt.resistance_score,
                           _COLORS_PT_RES, '阻力')

    # 绘制支撑区间（下平台）
    if pt.support_price > 0:
        _draw_one_platform(ax, start, end,
                           pt.support_price,
                           pt.support_zone_high,
                           pt.support_zone_low,
                           pt.support_touches,
                           pt.support_score,
                           _COLORS_PT_SUP, '支撑')


def _draw_one_platform(ax, start, end, price, zone_high, zone_low,
                        touches, score, color, label_prefix):
    """绘制单个平台区间: 半透明矩形 + 中心虚线 + 触碰点"""
    is_active = score.value >= GradeScore.B.value
    alpha_rect = 0.12 if is_active else 0.06
    alpha_line = 0.3
    ls_edge = '-' if is_active else '--'
    lw_edge = 1.0 if is_active else 0.6

    # 半透明矩形区间
    if zone_high > 0 and zone_low > 0:
        rect = patches.Rectangle(
            (start, zone_low), end - start, zone_high - zone_low,
            linewidth=lw_edge, linestyle=ls_edge,
            edgecolor=color, facecolor=color, alpha=alpha_rect,
            zorder=4
        )
        ax.add_patch(rect)

    # 中心价点线
    ax.plot([start, end], [price, price],
            color=color, linewidth=0.6, linestyle=':', alpha=alpha_line, zorder=5)

    # 触碰点
    if touches:
        for tp in touches:
            local_idx = tp[0]
            tp_price = tp[1]
            global_idx = start + local_idx
            ax.plot(global_idx, tp_price, 'o',
                    color=color, markersize=4, alpha=0.6, zorder=6)

    # 标签
    score_str = str(score)
    if zone_high > 0 and zone_low > 0:
        label = f"{label_prefix} {zone_low:.2f}~{zone_high:.2f} ({score_str})"
    else:
        label = f"{label_prefix} {price:.2f} ({score_str})"
    if score == GradeScore.C:
        label += " x"
    ax.text(end + 1, price, label,
            fontproperties=_FONT, color=color,
            fontsize=8, va='center', ha='left', zorder=10)



# ─── 底部评分摘要 ───

def _draw_summary(fig, card: ScoreCard):
    """在图表底部边距绘制精简结论行"""
    if card.conclusion_lines:
        n = min(len(card.conclusion_lines), 2)
        for i, line in enumerate(card.conclusion_lines[:n]):
            y_pos = 0.06 - i * 0.025
            if line.startswith("看多") or line.startswith("待定(多)"):
                color = '#27AE60'
            elif line.startswith("看空") or line.startswith("待定(空)"):
                color = '#E74C3C'
            elif line.startswith("备注"):
                color = '#888888'
            else:
                color = '#F39C12'
            fig.text(0.08, y_pos, line,
                     fontproperties=_FONT, fontsize=9, color=color,
                     va='center')
    else:
        grade_color = '#27AE60' if card.overall_passed else '#E74C3C'
        grade_text = f"综合: {card.weighted_score:.2f}分  [{card.overall_grade}]"
        fig.text(0.08, 0.05, grade_text,
                 fontproperties=_FONT, fontsize=9, color=grade_color,
                 va='center', fontweight='bold')
