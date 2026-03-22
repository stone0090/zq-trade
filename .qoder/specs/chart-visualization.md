# Chart Visualization for Six-Dimensional Analysis

## Context

用户需要将六维分析的判定结果以图表形式直观展示，便于逐个条件验证分析是否正确。图表功能通过 `--chart` 开关控制，默认不生成。

所有维度标注在一张K线图上，底部附评分汇总。

## Files to Modify

| File | Action | Description |
|------|--------|-------------|
| `main.py` | 修改 | 添加 `--chart` 参数，调用图表生成 |
| `src/report/charger.py` | **新建** | 图表生成核心模块 |

## Implementation

### 1. `main.py` — 添加 `--chart` CLI 参数

在 `p_analyze` 参数区添加：
```python
p_analyze.add_argument('--chart', action='store_true', help='生成K线分析图表（PNG）')
```

在 `cmd_analyze()` 中 `print_score_card(card)` 之后添加：
```python
if args.chart:
    from src.report.charger import generate_chart
    path = generate_chart(df, card)
    print(f"图表已保存: {path}")
```

### 2. `src/report/charger.py` — 图表生成模块

#### 模块结构
```
generate_chart(df, card, output_dir="charts") -> str   # 主入口
_build_chart(df, card) -> Figure                        # 图表构建
_setup_font() -> FontProperties                         # 中文字体
_draw_dl(ax, dl, df)           # DL: 半透明矩形框 + 上下边界虚线
_draw_pt(ax, pt, dl)           # PT: 粗水平线 + 触碰圆点
_draw_ty(ax, ty)               # TY: 淡黄色背景色带
_draw_dn(ax, dn, df)          # DN: 突破K线箭头标记
_draw_sf(ax, sf, dn, dl)      # SF: 释放级别文字
_draw_summary(ax, card, font)  # 底部六维评分汇总
_score_color(score) -> str     # 评分→颜色映射
```

#### 图表布局 (GridSpec 3行)
- **主面板 (70%)**: mplfinance K线蜡烛图 + 各维度叠加标注
- **成交量面板 (15%)**: 成交量柱状图
- **评分摘要 (15%)**: 六维评分文字 + 综合评级

使用 `mplfinance.plot()` 的 `external_axes` 模式，先创建 `fig + GridSpec`，再传入 axes。

#### K线样式
自定义 marketcolors: 红涨绿跌（A股习惯）：
```python
mc = mpf.make_marketcolors(up='#E74C3C', down='#27AE60', edge='inherit', wick='inherit', volume='in')
style = mpf.make_mpf_style(marketcolors=mc, gridstyle=':', y_on_right=True)
```

#### 各维度绘制方案

| 维度 | 图形元素 | 颜色 |
|------|----------|------|
| DL | `patches.Rectangle` 半透明框 + 上下边界虚线 + 标签 | 蓝色 `#4A90D9` |
| PT | 粗水平线 `ax.plot()` + 触碰点 `scatter()` + 价格标签 | 红色 `#E74C3C` |
| LK | 不绘制图形（仅底部文字展示） | - |
| TY | `ax.axvspan()` 背景色带 + 标签 | 橙色 `#F39C12` |
| DN | 箭头 `▲`/`▼` 在触发K线处 + 力度文字；pending 时文字提示 | 绿/红 |
| SF | 释放级别文字标注 | 绿/橙/红 |

#### 索引映射（关键）

| 数据来源 | 索引含义 | 转x轴坐标 |
|----------|---------|-----------|
| `dl.structure_start/end_idx` | df iloc 位置 | 直接使用 |
| `pt.touch_points[i][0]` | struct_df 局部索引 | **+ dl.structure_start_idx** |
| `ty.squeeze_start/end_idx` | df iloc 位置 | 直接使用 |
| `dn.trigger_idx` | df iloc 位置 | 直接使用 |

#### 中文字体处理
- Windows 字体查找: `msyh.ttc` → `simhei.ttf` → fallback
- `rcParams['font.sans-serif']` 全局设置 + 每个 text 元素显式 `fontproperties`
- `rcParams['axes.unicode_minus'] = False`

#### 边界情况
- DL 失败 (`early_terminated`): 绘K线 + DL红色虚线框（如有数据），其余跳过
- DN pending: 不画箭头，右侧文字 "等待触发"
- PT=C / TY=C: 仍绘制但用灰色虚线，标注 "(不满足)"
- touch_points 为空: 跳过触碰点绘制

### 3. 依赖安装

```bash
venv\Scripts\pip.exe install matplotlib mplfinance
```

requirements.txt 已包含这两个库。

## Verification

```bash
# 安装依赖
venv\Scripts\pip.exe install matplotlib mplfinance

# 生成图表测试
python main.py analyze 600802 --csv data/600802_hourly.csv --chart

# 验证输出
# 1. charts/ 目录下生成 PNG 文件
# 2. 图表包含: K线蜡烛图 + DL蓝色区间框 + PT红色平台线 + TY橙色背景 + 底部评分
# 3. 中文正确显示
# 4. 不加 --chart 时不生成图表
```
