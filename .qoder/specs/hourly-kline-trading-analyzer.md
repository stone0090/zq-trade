# 小时K线六维打分开仓分析工具 - 实现方案

## Context

用户有一套成熟的中线波段交易体系，包含6个评分维度（DL/PT/LK/TY/DN/SF），目前靠人工盯盘判断。需要将这套体系编程实现为一个自动化分析工具，输入股票代码后自动获取小时K线数据，按6个维度逐项量化打分，输出结构化的终端报告和操作建议。

## 架构设计

全新项目结构（独立于 `old/` 目录），数据获取复用 akshare 调用模式：

```
src/
├── __init__.py
├── analyzer/
│   ├── __init__.py
│   ├── base.py           # 评分枚举、Result数据类、AnalyzerConfig配置
│   ├── structure.py      # DL: 独立结构检测（90+K线盘整区间）
│   ├── platform.py       # PT: 平台位/颈线位检测（3+支点）
│   ├── contour.py        # LK: 轮廓质量评估（平滑度/均匀性）
│   ├── squeeze.py        # TY: 统一区间检测（尾部压缩区）
│   ├── momentum.py       # DN: 动能分析（突破K线力度）
│   ├── release.py        # SF: 释放级别评估（前置行情）
│   └── scorer.py         # 综合评分引擎（编排6维度+汇总）
├── data/
│   ├── __init__.py
│   └── fetcher.py        # 数据获取（akshare小时K线+CSV缓存）
├── utils/
│   ├── __init__.py
│   └── helpers.py        # ATR/线性回归/价格聚类等公用函数
└── report/
    ├── __init__.py
    └── printer.py        # 终端报告格式化输出
main.py                   # CLI入口
```

## 数据流

```
CLI: python main.py analyze 000626
  → fetcher.py: 获取小时K线 → pd.DataFrame(OHLCV, DatetimeIndex)
  → scorer.py: 按依赖链编排分析
      DL → PT → LK → TY → DN → SF
  → printer.py: 终端输出六维打分报告 + 操作建议
```

## 各模块实现要点

### 1. `base.py` - 基础数据结构

- `GradeScore` 枚举: S/A/B/C
- `ReleaseLevel` 枚举: FIRST/SECOND/THIRD
- 每个维度对应一个 Result dataclass（含 score/passed/reasoning/metrics）
- `AnalyzerConfig` dataclass: 集中管理所有量化阈值，便于调优
- `ScoreCard`: 汇总6个维度结果 + 综合评级 + 操作建议

### 2. `fetcher.py` - 数据获取

- `fetch_hourly_kline(symbol, start, end)`: 调用 akshare `stock_zh_a_hist_min_em(period='60')` 获取小时K线
- 自动分批拉取（akshare 单次有数据量限制），拼接为完整 DataFrame
- CSV 缓存机制：拉取后存本地 `data/` 目录，再次调用优先读缓存
- 支持 `load_from_csv(path)` 直接加载本地数据

### 3. `structure.py` - DL 独立结构检测

**算法**: 滑动窗口线性回归 → 趋势/盘整分段 → 盘整区间提取

1. 对收盘价用20根窗口计算归一化斜率
2. 斜率绝对值 < 0.02%/K线 → 标记为盘整段
3. 从最新数据往前，找到最近的连续盘整区间（允许≤3根趋势噪声）
4. 上下边界 = High的95分位 / Low的5分位
5. **硬规则**: K线数 ≥ 90 → S通过，< 90 → FAIL
6. **缺陷检测**: 前趋势急跌（斜率 > 0.15%/K线）标记瑕疵；结构右倾（斜率 > 0.03%/K线）标记瑕疵
7. **经验覆盖**: 70-89根但价格分布标准差/均价 < 2% → 条件通过

### 4. `platform.py` - PT 平台位/颈线位

**算法**: 价格聚类 → 支点计数 → 穿透检验

1. 结构区间内所有实体价格（Open/Close）做直方图聚类（bin宽度=ATR*0.1），找频次最高的N个候选平台位
2. 对每个候选位，用容忍带宽(ATR*0.15)统计有效触碰次数（相邻触碰需间隔≥5根K线）
3. 检查是否有实体完全穿过平台位（body_low < P-tol 且 body_high > P+tol）
4. 检查结构尾部10根K线有无大K线能量释放
5. 验证第3次触碰前是否有≥8根K线的远离调整

**评分**: S(5+触碰无穿透) / A(4+触碰或轻微穿透) / B(3+触碰≤1穿透) / C(不足3触碰)

### 5. `contour.py` - LK 轮廓质量

**算法**: 轨道平滑度 + K线均匀性 + 异常检测

1. 10根窗口计算上轨(High rolling max)和下轨(Low rolling min)
2. 上下轨一阶差分标准差 → 平滑度（归一化）
3. K线振幅变异系数CV = std(range)/mean(range)
4. 异常K线占比（range > mean+2*std）
5. 综合质量分 = 0.4*(1-平滑度) + 0.35*(1-CV归一化) + 0.25*(1-异常占比)

**评分**: S(≥0.80) / A(≥0.60) / B(≥0.40) / C(<0.40)，窄结构(振幅<3%)阈值上移0.1

### 6. `squeeze.py` - TY 统一区间

**算法**: 结构尾部小K线连续序列搜索

1. 以结构ATR均值为基准，标记振幅 < ATR*0.5 的K线为"小K线"
2. 从尾部往前扫描最长连续小K线序列（允许夹杂1根非小K线）
3. 对序列做线性回归，检验斜率 < 0.02%/K线
4. 检查序列末端与触发K线的间距 ≤ 1根

**评分**: S(6+根,斜率<0.01%,gap≤1) / A(4+根,斜率<0.02%) / B(3+根,斜率<0.03%) / C(不足3根或斜率过大)

### 7. `momentum.py` - DN 动能

**算法**: 突破K线力度对比统一区间

1. 识别收盘价突破结构上沿/下沿的K线作为触发K线
2. 力度比 = 触发K线实体 / squeeze平均振幅
3. 若单根未完全突破，允许2-3根合并（merged_count越多评分越低）
4. 检查是否突破PT检测到的平台位
5. 成交量放大倍数 = 触发K量 / squeeze平均量

**评分**: S(单根,力度≥3x,突破平台,放量>2x) / A(单根,力度≥2x,突破平台) / B(2根合并或力度1.5-2x) / C(3根合并或力度<1.5x)

### 8. `release.py` - SF 释放级别

**算法**: 结构结束到触发K线之间的价格运动幅度

1. 观察区间 = structure_end ~ trigger_idx
2. release_pct = 该区间最大偏移 / 结构边界价格 * 100%

**评分**: 1st(release<1%且≤2根) / 2nd(1%-3%或3-8根) / 3rd(>3%或>8根)

### 9. `scorer.py` - 综合评分引擎

**编排**: DL(不通过则提前终止) → PT → LK → TY → DN → SF

**一票否决**: DL=FAIL / PT=C / DN=C / SF=3rd → 不合格

**加权评级**(S=4,A=3,B=2,C=1): PT*0.30 + DN*0.30 + TY*0.25 + LK*0.15
- ≥3.5: 优秀开仓机会
- ≥2.8: 合格开仓机会
- ≥2.0: 勉强合格需谨慎
- <2.0: 不建议开仓

**操作建议** = SF级别 × 综合评级

### 10. `printer.py` - 终端报告

Unicode框线美观输出，每个维度显示：评分等级 + 关键数值 + 推理说明 + 瑕疵警告。底部汇总综合评级和操作建议。

### 11. `main.py` - CLI入口

```
python main.py analyze <symbol> [--start YYYY-MM-DD] [--end YYYY-MM-DD] [--csv path] [--no-cache] [--verbose]
```

## 实现顺序

1. `base.py` + `helpers.py` (基础层)
2. `fetcher.py` (数据层)
3. `structure.py` → `platform.py` → `contour.py` → `squeeze.py` → `momentum.py` → `release.py` (分析层，按依赖链)
4. `scorer.py` + `printer.py` (集成层)
5. `main.py` (入口)

## 验证方式

1. 用一只具体股票（如000626）运行 `python main.py analyze 000626`
2. 检查数据是否正确获取（K线数量、时间范围）
3. 检查各维度评分输出是否合理、reasoning是否可读
4. 对比人工判断验证评分是否与交易体系一致
5. 测试边界情况：数据不足、无平台位、无squeeze区、未出现突破等
