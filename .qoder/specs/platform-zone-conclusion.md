# 平台位区间化 + 结论格式精简

## Context
当前平台位(阻力/支撑)是单一价格线，不够直观。实际交易中平台位是一个价格区间。
同时，结论输出过于冗长，需要精简为 "看多：DLS/PTA/LKB/TYS/DNS/1st 结论：xxx 原因：xxx" 格式，
当两侧平台都有效时支持双结论（确定方向为主结论，另一侧为备注）。

---

## 1. base.py — 数据结构变更

**PlatformResult 新增 zone 字段**（在对应 price 字段后面添加）:
```python
# 激活平台区间
platform_zone_high: float = 0.0
platform_zone_low: float = 0.0

# 阻力区间
resistance_zone_high: float = 0.0
resistance_zone_low: float = 0.0

# 支撑区间
support_zone_high: float = 0.0
support_zone_low: float = 0.0
```

**ScoreCard 新增结论字段**:
```python
conclusion_lines: list = field(default_factory=list)  # 精简结论行
```

保留 `overall_grade` / `action_recommendation` 向后兼容。

---

## 2. platform.py — 区间检测

**区间定义**: `zone = center ± tolerance` (tolerance = ATR * 0.15)

**`_find_best_candidate`**: 在 best 字典中添加 `zone_high`/`zone_low`:
```python
best['zone_high'] = center_price + tolerance
best['zone_low'] = center_price - tolerance
```

**`analyze_platform`**: 存储结果时赋值 zone 字段:
```python
result.resistance_zone_high = best_resistance['zone_high']
result.resistance_zone_low = best_resistance['zone_low']
```

**`_set_active`**: 同步设置 `platform_zone_high`/`platform_zone_low`。

**reasoning 输出**: "阻力区间: 0.120~0.126 (中心 0.123)" 替代 "阻力位: 0.123"。

---

## 3. momentum.py — 突破判定改用区间边界

第111-124行，突破确认改为:
- 向上突破: `trigger_close > resistance_zone_high` (突破阻力区间上沿)
- 向下突破: `trigger_close < support_zone_low` (跌破支撑区间下沿)
- fallback: 若 zone 值为 0 退回 `platform_price`

---

## 4. scorer.py — 结论生成

在 `_finalize_card` 末尾新增 `_build_conclusions(card)` 函数:

**分数串格式**: `DL{S|F}/PT{S|A|B|C}/LK{S|A|B|C}/TY{S|A|B|C|?}/DN{S|A|B|C|?}/{1st|2nd|3rd}`

**结论生成逻辑**:

| 场景 | 输出 |
|------|------|
| DL不通过 | 1行: `DLF — 不满足独立结构条件` |
| DN确定方向 + 两侧平台有效 | 主结论(确定方向) + 备注(另一侧参考) |
| DN确定方向 + 仅一侧有效 | 1行主结论 |
| DN pending + 两侧有效 | 2行: "待定(多)" + "待定(空)" |
| DN pending + 仅一侧有效 | 1行: "待定" |
| 一票否决 | 1行: 方向+分数串+否决原因 |

**结论文本**:
- 通过 + 1st → "可执行"
- 通过 + 2nd → "等回踩"
- 通过 + pending → "等突破"
- 否决 → "不建议"

---

## 5. charger.py — 区间矩形绘制

**`_draw_one_platform` 改造**:
- 新增参数 `zone_high`, `zone_low`
- 绘制半透明矩形: `patches.Rectangle((start, zone_low), end-start, zone_high-zone_low)`
  - 阻力: 红色 alpha=0.12 (活跃) / 0.06 (非活跃)
  - 支撑: 蓝色 alpha=0.12 / 0.06
- 中心价改为点线: `linestyle=':'`, `linewidth=0.6`, `alpha=0.3`
- 标签: "阻力 0.120~0.126 (A)"

**`_draw_pt` 调参**: 从 pt 对象取 zone 字段传给 `_draw_one_platform`。

**`_draw_summary` 更新**: 用 `card.conclusion_lines` 替代冗长的 action_recommendation。

---

## 6. printer.py — 终端输出精简

**PT 部分**: 单价改为区间显示:
```
上平台(阻力): 6.490~6.544  (5次触碰/2次穿透, B)
```

**Footer 部分**: 在综合评级之前输出 conclusion_lines:
```
  看多：DLS/PTB/LKB/TYS/DNS/1st  结论：可执行  原因：单根突破力度3.2x
  备注：DLS/PTA/LKB/TYS/--/--   支撑区间 6.10~6.15 参考
```
看多绿色，看空红色，待定黄色。

---

## 实施顺序

1. `base.py` — 添加字段 (无破坏性)
2. `platform.py` — zone 计算+存储
3. `momentum.py` — zone 边界突破判定
4. `scorer.py` — 结论生成函数
5. `charger.py` — 区间矩形绘制
6. `printer.py` — 精简输出

## 关键文件
- `src/analyzer/base.py`
- `src/analyzer/platform.py`
- `src/analyzer/momentum.py`
- `src/analyzer/scorer.py`
- `src/report/charger.py`
- `src/report/printer.py`

## 验证方法
1. `python main.py analyze 600802 --csv data/600802_hourly.csv` 确认终端输出新格式
2. `python main.py analyze 600802 --csv data/600802_hourly.csv --chart` 确认图表区间矩形
3. 检查 zone_high/zone_low 值 = center ± ATR*0.15
4. 检查双结论在 DN 确定方向后正确显示主结论+备注
