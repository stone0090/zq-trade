# zq-trade

基于小时K线的六维量化开仓条件分析框架。通过 DL/PT/LK/TY/DN/SF 六个维度对股票进行系统化评估，判断是否满足开仓条件并给出操作建议。

## 六维分析体系

| 维度 | 名称 | 说明 |
|------|------|------|
| **DL** | 独立结构 | 识别盘整区间，检测价格是否形成有效的横盘收敛结构 |
| **PT** | 平台位 | 检测阻力位和支撑位区间，评估触碰次数和有效性 |
| **LK** | 轮廓质量 | 评估结构内K线的平整度和异常波动情况 |
| **TY** | 挤压收敛 | 检测布林带/ATR等指标是否出现收窄信号 |
| **DN** | 动能突破 | 检测是否出现突破触发信号及方向判断 |
| **SF** | 释放级别 | 评估介入时机 (1st 直接执行 / 2nd 等回踩 / 3rd 需新结构) |

评分等级: **S** (优秀) > **A** (良好) > **B** (合格) > **C** (不合格)

## 项目结构

```
zq-trade/
├── main.py                  # CLI 入口
├── src/
│   ├── analyzer/            # 六维分析核心
│   │   ├── base.py          # 数据结构定义 (ScoreCard/GradeScore/AnalyzerConfig)
│   │   ├── structure.py     # DL 独立结构检测
│   │   ├── platform.py      # PT 平台位检测
│   │   ├── contour.py       # LK 轮廓质量评分
│   │   ├── squeeze.py       # TY 挤压收敛检测
│   │   ├── momentum.py      # DN 动能突破检测
│   │   ├── release.py       # SF 释放级别评估
│   │   └── scorer.py        # 综合评分引擎
│   ├── data/
│   │   └── fetcher.py       # 数据获取 (Sina/akshare 双数据源，本地缓存)
│   └── report/
│       ├── charger.py       # K线分析图表生成 (matplotlib)
│       └── printer.py       # 终端格式化报告输出
├── batch_compare.py         # 批量验证: 算法输出 vs 人工标注
├── batch_charts.py          # 批量生成所有案例的分析图表
├── data/
│   └── labeled_cases.csv    # 人工标注的验证案例集
├── requirements.txt
└── environment.yml
```

## 安装

```bash
# 创建虚拟环境
python -m venv venv
venv\Scripts\activate  # Windows
# source venv/bin/activate  # Linux/Mac

# 安装依赖
pip install -r requirements.txt
```

> TA-Lib 需要预先安装 C 库，Windows 可从 [这里](https://github.com/cgohlke/talib-build/releases) 下载预编译 whl 文件。

## 使用

### 单股分析

```bash
# 基本分析
python main.py analyze 600802

# 指定截止日期 + 生成图表
python main.py analyze 605277 --end 2026-01-15 --chart

# 指定K线数量 + 禁用缓存
python main.py analyze 603978 --bars 600 --no-cache
```

输出示例:
```
看多: DLS/PTA/LKB/TYC/DNC/1st  结论: 不建议  原因: TY(挤一挤吧) + C
```

### 批量验证

```bash
# 对比算法输出 vs 人工标注
python batch_compare.py

# 批量生成分析图表
python batch_charts.py
```

## 数据源

- **A股**: Sina Finance (主) + akshare/东方财富 (备)
- **美股**: yfinance
- 本地缓存于 `data/cn/` 和 `data/us/`，支持增量更新

## 市场规则

- A股仅做多方向分析
- 美股支持双向 (做多/做空)
