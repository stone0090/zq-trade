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

from core.types import (
    ScoreCard, GradeScore, ReleaseLevel
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
            return FontProperties(fname=path, size=12)
    # fallback
    return FontProperties(size=12)


_FONT = _setup_font()
_FONT_TITLE = _setup_font()
_FONT_TITLE._size = 16

# 全局 rcParams 兜底
matplotlib.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'sans-serif']
matplotlib.rcParams['axes.unicode_minus'] = False


# ─── 颜色方案 ───

_COLORS = {
    'dl': '#4A90D9',
    'pt': '#9B59B6',
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

    fig = plt.figure(figsize=(36, 13.3))
    gs = GridSpec(2, 1, height_ratios=[7, 1.5], hspace=0.05, figure=fig)
    fig.subplots_adjust(left=0.03, right=0.95, bottom=0.10)  # 压缩左边距，右侧留给PT标签

    ax_main = fig.add_subplot(gs[0])
    ax_vol = fig.add_subplot(gs[1], sharex=ax_main)

    # K线样式: A股红涨绿跌，港股/美股绿涨红跌
    market = getattr(card, 'market', 'cn') or 'cn'
    if market == 'cn':
        up_color, down_color = '#E74C3C', '#27AE60'
    else:
        up_color, down_color = '#27AE60', '#E74C3C'
    mc = mpf.make_marketcolors(
        up=up_color, down=down_color,
        edge='inherit', wick='inherit',
        volume={'up': up_color, 'down': down_color}
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

    # 叠加标注: DL / PT / TY / DN
    if card.dl_result:
        _draw_dl(ax_main, card.dl_result, df, lk=card.lk_result)

    if card.pt_result and not card.early_terminated:
        _draw_pt(ax_main, card.pt_result, card.dl_result, market=card.market)

    if card.ty_result and card.dl_result and not card.ty_result.pending:
        _draw_ty(ax_main, card.ty_result, card.dl_result, df)

    if card.dn_result and card.dl_result:
        _draw_dn(ax_main, ax_vol, card.dn_result, card.dl_result, df)

    # 底部精简结论 (用 fig.text 直接写在图表底部边距)
    _draw_summary(fig, card)

    # 隐藏成交量面板的x轴标签 (与主面板共享)
    plt.setp(ax_main.get_xticklabels(), visible=False)

    return fig


# ─── DL 独立结构 ───

def _draw_dl(ax, dl, df, lk=None):
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

    # 中间标签: DL + LK
    mid_x = (start + end) / 2
    label_dl = f"DL {dl.kline_count}K ({dl.score})"
    ax.text(mid_x, ruler_y - y_range * 0.008, label_dl,
            fontproperties=_FONT, color=color,
            fontsize=10, ha='center', va='top', zorder=10)

    # LK 标签紧跟 DL 右侧，用自己评分的颜色
    if lk:
        lk_color = _score_color(lk.score)
        label_lk = f"  LK({lk.score})"
        ax.text(end, ruler_y - y_range * 0.008, label_lk,
                fontproperties=_FONT, color=lk_color,
                fontsize=10, ha='left', va='top', zorder=10)


# ─── PT 平台位 ───

_COLORS_PT_RES = '#9B59B6'  # 阻力位紫色
_COLORS_PT_SUP = '#2980B9'  # 支撑位蓝色


def _draw_pt(ax, pt, dl, market='cn'):
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

    # 绘制支撑区间（下平台）— A股跳过
    if pt.support_price > 0 and market != 'cn':
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
                    color=color, markersize=6, alpha=0.6, zorder=6)

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
            fontsize=10, va='center', ha='left', zorder=10)



# ─── TY 统一区间 ───

def _draw_ty(ax, ty, dl, df):
    """
    绘制 TY 挤压区间：在K线图上用半透明橙色矩形标注小K线密集区。
    """
    if ty.squeeze_length == 0:
        return

    start = ty.squeeze_start_idx
    end = ty.squeeze_end_idx
    color = _COLORS['ty']  # 橙色

    # 获取挤压区间的价格范围
    seg = df.iloc[start:end + 1] if end + 1 <= len(df) else df.iloc[start:]
    if len(seg) == 0:
        return

    y_lo = seg['Low'].min()
    y_hi = seg['High'].max()
    pad = (y_hi - y_lo) * 0.15  # 上下留一点余量
    y_lo -= pad
    y_hi += pad

    # 半透明矩形
    if ty.pending:
        alpha = 0.05
    elif ty.score.value >= GradeScore.A.value:
        alpha = 0.15
    else:
        alpha = 0.08
    rect = patches.Rectangle(
        (start, y_lo), end - start, y_hi - y_lo,
        linewidth=1.0, linestyle='--',
        edgecolor=color, facecolor=color, alpha=alpha,
        zorder=3
    )
    ax.add_patch(rect)

    # 左侧竖线标注起点
    ax.plot([start, start], [y_lo, y_hi],
            color=color, linewidth=0.8, linestyle='--', alpha=0.4, zorder=4)

    # 标签在挤压区上方
    if ty.pending:
        label = f"TY {ty.squeeze_length}K (待定)"
    else:
        label = f"TY {ty.squeeze_length}K ({ty.score})"
    ax.text((start + end) / 2, y_hi + pad * 0.3, label,
            fontproperties=_FONT, color=color,
            fontsize=9, ha='center', va='bottom', zorder=10,
            bbox=dict(boxstyle='round,pad=0.15', facecolor='white',
                      edgecolor=color, alpha=0.7))


# ─── DN 动能 ───

def _draw_dn(ax, ax_vol, dn, dl, df):
    """
    绘制 DN 突破K线标注：
    - 非Pending时：在触发K线处画竖线 + 三角箭头标记方向
    - Pending时：在结构末端标注 "DN?" 待定标记
    """
    if dn.pending:
        # Pending: 在结构末端标注待定
        end = dl.structure_end_idx
        y_low, y_high = ax.get_ylim()
        y_range = y_high - y_low
        ax.text(end + 2, y_low + y_range * 0.12, "DN?",
                fontproperties=_FONT, color='#999999',
                fontsize=11, ha='left', va='center', zorder=10,
                fontweight='bold',
                bbox=dict(boxstyle='round,pad=0.2', facecolor='#f0f0f0',
                          edgecolor='#999999', alpha=0.8))
        return

    if dn.trigger_idx < 0 or dn.trigger_idx >= len(df):
        return

    idx = dn.trigger_idx
    is_bull = (dn.direction == 'bullish')
    color = _COLORS['dn_bull'] if is_bull else _COLORS['dn_bear']

    # 获取触发K线的价格
    row = df.iloc[idx]
    trigger_high = row['High']
    trigger_low = row['Low']
    trigger_close = row['Close']

    # 竖线贯穿触发K线
    y_low, y_high = ax.get_ylim()
    y_range = y_high - y_low
    line_top = trigger_high + y_range * 0.06
    line_bottom = trigger_low - y_range * 0.06

    ax.plot([idx, idx], [line_bottom, line_top],
            color=color, linewidth=1.5, linestyle='-', alpha=0.6, zorder=7)

    # 三角箭头标记方向
    if is_bull:
        # 向上三角 ▲
        arrow_y = line_top
        ax.plot(idx, arrow_y, marker='^', color=color,
                markersize=13, zorder=8)
    else:
        # 向下三角 ▼
        arrow_y = line_bottom
        ax.plot(idx, arrow_y, marker='v', color=color,
                markersize=13, zorder=8)

    # 如果是合并K线，用浅色背景标注合并范围
    if dn.merged_count > 1:
        merge_start = max(0, idx - dn.merged_count + 1)
        merge_seg = df.iloc[merge_start:idx + 1]
        m_lo = merge_seg['Low'].min()
        m_hi = merge_seg['High'].max()
        m_pad = (m_hi - m_lo) * 0.1
        rect = patches.Rectangle(
            (merge_start, m_lo - m_pad), idx - merge_start + 1, m_hi - m_lo + 2 * m_pad,
            linewidth=0.8, linestyle=':',
            edgecolor=color, facecolor=color, alpha=0.06,
            zorder=3
        )
        ax.add_patch(rect)

    # 标签
    parts = [f"DN({dn.score})"]
    if dn.merged_count > 1:
        parts.append(f"{dn.merged_count}合")
    parts.append(f"力度{dn.force_ratio:.1f}x")
    if dn.broke_platform:
        parts.append("破位")
    label = " ".join(parts)

    label_y = line_top + y_range * 0.015 if is_bull else line_bottom - y_range * 0.015
    va = 'bottom' if is_bull else 'top'
    ax.text(idx, label_y, label,
            fontproperties=_FONT, color=color,
            fontsize=9, ha='center', va=va, zorder=10,
            bbox=dict(boxstyle='round,pad=0.15', facecolor='white',
                      edgecolor=color, alpha=0.7))


# ─── 底部评分摘要 ───

def _draw_summary(fig, card: ScoreCard):
    """在图表底部边距绘制结论行（只保留每对的极简行）"""
    if card.conclusion_lines:
        # 只取极简行（偶数索引: 0, 2, 4...）
        simple_lines = [card.conclusion_lines[i] for i in range(0, len(card.conclusion_lines), 2)]
        y_start = 0.05
        for i, line in enumerate(simple_lines):
            y_pos = y_start - i * 0.04

            if line.startswith("看多") or line.startswith("待定(多)"):
                color = '#27AE60'
            elif line.startswith("看空") or line.startswith("待定(空)"):
                color = '#E74C3C'
            elif line.startswith("备注"):
                color = '#888888'
            else:
                color = '#F39C12'
            fig.text(0.95, y_pos, line,
                     fontproperties=_FONT, fontsize=20, color=color,
                     va='center', ha='right', fontweight='bold')
    else:
        grade_color = '#27AE60' if card.overall_passed else '#E74C3C'
        grade_text = f"综合: {card.overall_grade}"
        fig.text(0.95, 0.05, grade_text,
                 fontproperties=_FONT, fontsize=20, color=grade_color,
                 va='center', ha='right', fontweight='bold')
