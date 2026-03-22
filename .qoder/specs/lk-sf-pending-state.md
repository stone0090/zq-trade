# LK/SF 级联待定状态 + 纯做多简化

## Context

当前六维分析系统中，LK（轮廓）和 SF（释放）始终会被计算，即使前置条件（DL 结构 + PT 阻力位）都不满足。这在概念上不合理：没有有效结构和平台位时，分析轮廓质量和释放级别毫无意义。

需要建立级联依赖：DL/PT → LK → SF，前置条件不满足时后续维度标记为"待定"。同时去掉支撑位/看空逻辑，统一按做多处理。

## 级联规则

```
DL=C AND PT.resistance_score=C → LK=待定, SF=待定
LK=待定 → SF=待定（级联传递）
LK=C（已评分但不通过）→ SF 正常计算（LK 有了就能判断 SF）
```

## 修改文件清单

### 1. `core/types.py` — 添加 pending 字段

**ContourResult** (第129行附近): 添加 `pending: bool = False`（在 score 之后）
**ReleaseResult** (第184行附近): 添加 `pending: bool = False`（在 score 之后）

模式参考：`SqueezeResult.pending` 和 `MomentumResult.pending`

---

### 2. `core/analyzer/scorer.py` — 核心逻辑（4处函数）

#### 2a. `run_full_analysis()` (第71-82行)

在 `_refine_dl_from_pt()` 之后、LK 计算之前，插入级联判定：

```python
# ─── 级联待定判定 ───
_dl_pt_insufficient = (dl.score == GradeScore.C
                       and pt.resistance_score == GradeScore.C)

if _dl_pt_insufficient:
    # DL+PT 都无效，LK/SF 无意义
    lk = ContourResult(pending=True,
                       reasoning=["DL=C且PT阻力=C，LK待定"])
    sf = ReleaseResult(pending=True,
                       reasoning=["LK待定，SF级联待定"])
else:
    # 正常计算 LK
    lk = analyze_contour(df, dl, config)
    if lk.pending:
        # LK 待定则 SF 级联待定
        sf = ReleaseResult(pending=True,
                           reasoning=["LK待定，SF级联待定"])
    else:
        # LK 已有评分（含C），正常计算 SF
        sf_direction = 'bullish'
        sf = analyze_release(df, dl, config,
                             direction=sf_direction, platform=pt)
```

同时删除原来第 76-81 行的 market 分支判断（`if market == 'cn'` / `else`），统一 `sf_direction = 'bullish'`。

#### 2b. `_determine_direction_from_pt()` (第112-125行)

简化为始终返回 bullish：

```python
def _determine_direction_from_pt(pt) -> str:
    """纯做多模式，始终返回 bullish"""
    return 'bullish'
```

#### 2c. `_determine_position()` (第279-350行)

在"DN pending → 等待"(第292行) 之后、"SF=3rd → 不做"(第296行) 之前，插入：

```python
# LK/SF pending → 等待
if card.lk_result and card.lk_result.pending:
    return "等待", "LK待定（结构/平台条件不足）"
if card.sf_result and card.sf_result.pending:
    return "等待", "SF待定（等待LK确定后评估）"
```

shortfalls 收集处（第318-325行）增加 pending 守卫：
- LK: `if not lk or lk.pending or lk.score.value < GradeScore.A.value`
- SF: `if sf.pending` 时 `shortfalls.append("SF=待定")`

#### 2d. `_build_conclusions()` (第353-472行)

**LK/SF 标签**（第379-383行）：

```python
# LK
if lk and lk.pending:
    lk_tag = "待定"
elif lk:
    lk_tag = _tag_with_reason(lk.score, _get_lk_reason(lk))
else:
    lk_tag = "?"

# SF
if sf and sf.pending:
    sf_tag = "待定"
elif sf:
    sf_tag = _tag_sf_with_reason(sf)
else:
    sf_tag = "--"
```

**方向逻辑简化**（第402-429行）：

移除所有 `has_support`、`bearish`、`bearish_reject` 路径，简化为：
- DN 已触发 + bullish → `directions = [('bullish', True)]`
- DN pending/无方向 + has_resistance → `directions = [('bullish', True)]`
- 否则 → `directions` 为空 → fallback "待定"行

**结论行生成**（第439-472行）：

移除 `bearish_reject` 分支（第441-447行）和 bearish 的 `else` 分支（第453-456行），只保留 bullish 路径。移除备注行中 support 区间信息。

---

### 3. `core/serializer.py` — extract_grades() (第67-78行)

LK（第67-71行）和 SF（第73-78行）增加 pending 检查，模式同 TY（第82-85行）：

```python
# LK
lk = card_dict.get('lk_result')
if lk:
    if lk.get('pending'):
        grades['lk_grade'] = '待定'
    else:
        grades['lk_grade'] = lk.get('score')

# SF
sf = card_dict.get('sf_result')
if sf:
    if sf.get('pending'):
        grades['sf_grade'] = '待定'
    else:
        grades['sf_grade'] = str(score_val) if score_val else None
```

---

### 4. `core/report/printer.py` — LK/SF 打印（第171-191行）

模式参考 TY（第197-198行）：

```python
# LK (第172行内)
if lk.pending:
    print(f"  LK 轮廓      {_bar(GradeScore.C)}  {_c('[ 待定 ]', 'yellow')}")
else:
    # 原有详细输出

# SF (第183行内)
if sf.pending:
    print(f"  SF 释放级别  {_bar(GradeScore.C)}  {_c('[ 待定 ]', 'yellow')}")
else:
    # 原有详细输出
```

---

### 5. `core/report/chart.py` — LK 标签（第216-221行）

```python
if lk and lk.pending:
    label_lk = "  LK(待定)"
    lk_color = '#999999'
else:
    # 原有逻辑
```

---

### 6. `web/templates/stock_detail.html`

**标注下拉框** — LK（第154行）和 SF（第160行）追加 `<option>待定</option>`，与 TY/DN 一致

**算法结果展示**（第517-518行）：
```javascript
{ key: 'LK', result: sc.lk_result, extra: sc.lk_result && sc.lk_result.pending ? '(待定)' : '' },
{ key: 'SF', result: sc.sf_result, extra: sc.sf_result && sc.sf_result.pending ? '(待定)' : '' },
```

---

### 不需要改动的文件

| 文件 | 原因 |
|------|------|
| `web/services/state_machine.py` | `_GRADE_ORDER` 已含 '待定'=0，所有比较天然兼容 |
| `web/templates/base.html` | `gradeTag()` 已能处理 '待定' 样式 |
| `web/database.py` | grade 字段为 TEXT，可存 "待定" |
| `core/analyzer/contour.py` | LK 分析函数本身不变，pending 由外部控制 |
| `core/analyzer/release.py` | SF 分析函数本身不变 |
| `core/analyzer/platform.py` | PT 仍检测两侧，只是决策时只用阻力位 |

## 实施顺序

1. `core/types.py` — 添加 pending 字段（基础依赖）
2. `core/analyzer/scorer.py` — 核心逻辑（级联判定 + 方向简化 + 仓位 + 结论）
3. `core/serializer.py` — 序列化适配
4. `core/report/printer.py` + `core/report/chart.py` — 展示适配
5. `web/templates/stock_detail.html` — 前端适配

## 验证

1. **语法检查**: `python -c "import py_compile; py_compile.compile('core/analyzer/scorer.py', doraise=True)"`
2. **级联触发**: 找一个 DL=C + PT 阻力=C 的股票，验证 LK/SF 显示"待定"
3. **级联不触发**: DL=S 的股票，验证 LK/SF 正常评分
4. **纯做多**: 美股也只输出"看多"方向，无"看空"/"备注"行
5. **启动服务**: `python start.py` 后访问详情页，检查六维展示
