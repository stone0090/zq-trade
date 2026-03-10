# ZQ-Trade

基于小时K线的六维量化开仓条件分析引擎。通过 DL/PT/LK/TY/DN/SF 六个维度对股票进行系统化评估，判断是否满足开仓条件并给出操作建议。

## Quick Start

```python
from core import analyze

card = analyze("600802")                          # A股
card = analyze("HIMS", end_date="2026-03-07")     # 美股 + 指定日期
card = analyze("02610", bars=400)                 # 港股 + 指定K线数
```

返回的 `ScoreCard` 包含六维分析结果、综合结论和仓位建议。

## 项目架构

项目由两部分组成：

| 模块 | 定位 | 说明 |
|------|------|------|
| `core/` | K线分析引擎 | 核心能力，面向 Agent 设计，一行 import 即可调用 |
| `web/` | 标注管理系统 | 面向人类，提供批量分析、人工标注、对比验证的 Web 界面 |

**依赖规则**: `web` 可以 import `core`，`core` 不可以 import `web`。

```
zq-trade/
├── core/                       # 核心分析引擎 (Agent API)
│   ├── __init__.py             # Public API 入口 (analyze, fetch_kline, ...)
│   ├── types.py                # 数据结构定义 (ScoreCard/GradeScore/AnalyzerConfig)
│   ├── serializer.py           # ScoreCard 序列化工具
│   ├── analyzer/               # 六维分析器
│   │   ├── scorer.py           # 综合评分引擎 (run_full_analysis)
│   │   ├── structure.py        # DL 独立结构检测
│   │   ├── platform.py         # PT 平台位检测
│   │   ├── contour.py          # LK 轮廓质量评分
│   │   ├── squeeze.py          # TY 挤压收敛检测
│   │   ├── momentum.py         # DN 动能突破检测
│   │   └── release.py          # SF 释放级别评估
│   ├── data/
│   │   └── fetcher.py          # 数据获取 (Sina/akshare/yfinance，本地缓存)
│   ├── report/
│   │   ├── printer.py          # 终端格式化报告
│   │   └── chart.py            # K线分析图表生成 (matplotlib)
│   └── utils/
│       └── helpers.py          # 通用工具函数
├── web/                        # 标注管理系统 (Web UI)
│   ├── app.py                  # FastAPI 应用
│   ├── config.py               # 配置
│   ├── database.py             # SQLite 数据库
│   ├── models.py               # 数据模型
│   ├── routes/                 # 路由
│   │   ├── tags.py             # 标签管理
│   │   ├── stocks.py           # 股票管理 (导入/分析/筛选)
│   │   └── labels.py           # 人工标注 + 导出
│   ├── services/
│   │   ├── analysis.py         # 分析服务 (调用 core 引擎)
│   │   └── export.py           # 数据导出
│   ├── templates/              # Jinja2 模板
│   └── static/                 # 静态资源
├── scripts/                    # CLI 工具脚本
│   ├── analyze.py              # 单股分析 CLI
│   ├── serve.py                # 启动 Web 服务
│   ├── batch_analyze.py        # 批量分析 + 图表
│   ├── batch_charts.py         # 批量生成图表
│   └── batch_compare.py        # 算法 vs 人工标注对比
├── docs/                       # 设计文档
│   ├── TRADING_RULES.md        # 交易规则说明
│   └── architecture.md         # 项目架构设计
├── data/                       # 数据目录
│   └── labeled_cases.csv       # 人工标注案例集
├── requirements.txt
└── environment.yml
```

## 六维分析体系

| 维度 | 名称 | 说明 | 评分 |
|------|------|------|------|
| **DL** | 独立结构 | 识别盘整区间，检测价格是否形成有效的横盘收敛结构 | S/A/B/C/F |
| **PT** | 平台位 | 检测阻力位和支撑位区间，评估触碰次数和有效性 | S/A/B/C |
| **LK** | 轮廓质量 | 评估结构内K线的平整度和异常波动情况 | S/A/B/C |
| **TY** | 挤压收敛 | 检测布林带/ATR等指标是否出现收窄信号 | S/A/B/C |
| **DN** | 动能突破 | 检测是否出现突破触发信号及方向判断 | S/A/B/C |
| **SF** | 释放级别 | 评估介入时机 (1st 直接执行 / 2nd 等回踩 / 3rd 需新结构) | 1st/2nd/3rd |

## Agent API

面向 Agent 设计的一站式接口：

```python
from core import analyze, scorecard_to_dict, extract_grades

# 一站式分析
card = analyze("600802")

# 获取结论
print(card.conclusion_lines)       # 综合结论
print(card.action_recommendation)  # 操作建议
print(card.position_size)          # 仓位建议

# 序列化为 dict/JSON
data = scorecard_to_dict(card)     # -> dict (可直接 json.dumps)
grades = extract_grades(data)      # -> {'dl_grade': 'S', 'pt_grade': 'A', ...}
```

底层 API（需要更细粒度控制时使用）：

```python
from core import fetch_kline, run_full_analysis, AnalyzerConfig, ScoreCard

df = fetch_kline("600802", bars=300)
config = AnalyzerConfig()
card = run_full_analysis(df, symbol="600802", config=config, market="cn")
```

## CLI 使用

```bash
# 单股分析
python scripts/analyze.py analyze 600802
python scripts/analyze.py analyze 605277 --end 2026-01-15 --chart
python scripts/analyze.py analyze 603978 --bars 600

# 批量分析 + 图表
python scripts/batch_analyze.py

# 批量生成标注案例图表
python scripts/batch_charts.py

# 算法 vs 人工标注对比
python scripts/batch_compare.py
```

## Web 标注系统

```bash
# 启动服务
python scripts/serve.py

# 访问 http://localhost:8000
```

功能：
- **股票导入**: 输入股票代码列表，可选指定截止日期和标签，支持自动触发分析
- **标签分组**: 多对多标签系统，股票可同时属于多个标签，支持按标签筛选和批量分析
- **六维评分**: 自动运行 DL/PT/LK/SF/TY/DN 六维分析，生成K线图表
- **人工标注**: 逐只标注六维评分 + 结论判断，支持快捷键操作（Ctrl+S/方向键/Ctrl+Enter）
- **数据导出**: 导出全部或按标签导出 CSV，兼容 `labeled_cases.csv` 格式
- **单只重分析**: 修改截止日期后可对单只股票触发重新分析

## 安装

```bash
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # Linux/Mac

pip install -r requirements.txt
```

> TA-Lib 需要预先安装 C 库，Windows 可从 [talib-build](https://github.com/cgohlke/talib-build/releases) 下载预编译 whl。

## 数据源

| 市场 | 数据源 | 缓存目录 |
|------|--------|----------|
| A股 | Sina Finance (主) + akshare (备) | `data/cn/` |
| 港股 | akshare | `data/hk/` |
| 美股 | yfinance | `data/us/` |

## 开发规范

1. **新增分析维度**: 在 `core/analyzer/` 下新增模块，在 `core/types.py` 中定义结果数据类，在 `scorer.py` 中集成
2. **新增 Web 功能**: 在 `web/routes/` 下新增路由，通过 `core` 的 Public API 调用分析引擎
3. **新增脚本工具**: 在 `scripts/` 下新增，使用 `from core import analyze` 调用引擎
4. **依赖方向**: `core` 保持独立，不依赖 `web`；`web` 和 `scripts` 通过 `core` 的 Public API 调用
5. **数据类型**: 所有分析相关的 dataclass / enum 定义在 `core/types.py`
6. **序列化**: ScoreCard 序列化统一使用 `core/serializer.py`
