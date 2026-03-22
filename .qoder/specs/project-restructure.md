# ZQ-Trade 项目目录重构计划

## Context

项目有两大能力：**K线分析引擎**（核心，未来面向 Agent 调用）和 **Web 标注系统**（面向人类管理/打标）。当前目录结构存在以下问题：

- `src/` 语义模糊，不表达"核心引擎"的定位
- `server/` 无法体现"面向人类"的区别
- 5 个脚本散落在根目录（main.py, start.py, batch_*.py）
- `base.py` 实为全局类型定义却藏在 `analyzer/` 下
- `charger.py` 命名不直观（实为 chart 图表模块）
- 缺少统一的公共 API 入口，Agent 调用需知道内部路径
- 序列化逻辑（`scorecard_to_dict`）放在 web 层而非核心引擎

**目标**：重构为 `core/`（面向 Agent）+ `web/`（面向人类）的清晰两域架构，提供一行导入即可分析的公共 API，并建立严格的开发规范。

---

## 新目录结构

```
zq-trade/
├── core/                              # 核心分析引擎（面向 Agent）
│   ├── __init__.py                    # Public API 入口
│   ├── types.py                       # 数据结构（原 src/analyzer/base.py 上提）
│   ├── serializer.py                  # ScoreCard → dict/JSON（从 web 提取）
│   ├── analyzer/                      # 六维分析器
│   │   ├── __init__.py
│   │   ├── scorer.py                  # 综合评分编排
│   │   ├── structure.py               # DL
│   │   ├── platform.py                # PT
│   │   ├── contour.py                 # LK
│   │   ├── squeeze.py                 # TY
│   │   ├── momentum.py                # DN
│   │   └── release.py                 # SF
│   ├── data/
│   │   ├── __init__.py
│   │   └── fetcher.py                 # K线数据获取
│   ├── report/
│   │   ├── __init__.py
│   │   ├── chart.py                   # 图表生成（原 charger.py）
│   │   └── printer.py                 # 终端输出
│   └── utils/
│       ├── __init__.py
│       └── helpers.py
│
├── web/                               # Web 标注系统（面向人类）
│   ├── __init__.py
│   ├── app.py
│   ├── config.py
│   ├── database.py
│   ├── models.py
│   ├── routes/                        # batches.py, stocks.py, labels.py
│   ├── services/                      # analysis.py（简化，调用 core）, export.py
│   └── templates/                     # 4个 HTML 模板
│
├── scripts/                           # CLI 工具脚本
│   ├── analyze.py                     # 单股分析（原 main.py）
│   ├── serve.py                       # 启动 Web 服务（原 start.py）
│   ├── batch_analyze.py
│   ├── batch_charts.py
│   └── batch_compare.py
│
├── data/                              # 数据存储
│   ├── labeled_cases.csv
│   ├── cn/ hk/ us/                    # K线缓存（gitignored）
│
├── doc/
│   └── TRADING_RULES.md
│
├── README.md                          # 重写
├── requirements.txt
└── .gitignore
```

---

## 实施步骤

### Step 1: 目录移动与重命名

使用 `git mv` 保留 git 历史：

```
git mv src core
git mv core/analyzer/base.py core/types.py
git mv core/report/charger.py core/report/chart.py
git mv server web
mkdir scripts
git mv main.py scripts/analyze.py
git mv start.py scripts/serve.py
git mv batch_analyze.py scripts/batch_analyze.py
git mv batch_charts.py scripts/batch_charts.py
git mv batch_compare.py scripts/batch_compare.py
```

清理 `__pycache__`：`find . -name __pycache__ -exec rm -rf {} +`

### Step 2: 创建 core/serializer.py

从 `web/services/analysis.py` 提取以下函数到 `core/serializer.py`：
- `_serialize()` — 递归序列化 dataclass/enum/numpy
- `scorecard_to_dict()` — ScoreCard → dict
- `extract_grades()` — 从 dict 提取各维度 grade

### Step 3: 创建 core/__init__.py（Public API）

提供 Agent 一行导入的便利接口：

```python
from core.data.fetcher import fetch_kline_smart as fetch_kline, detect_market, get_stock_name
from core.analyzer.scorer import run_full_analysis
from core.types import ScoreCard, AnalyzerConfig, GradeScore, ReleaseLevel
from core.serializer import scorecard_to_dict

def analyze(symbol, end_date=None, bars=300, config=None):
    """一站式分析：获取数据 → 六维分析 → 返回 ScoreCard"""
    market = detect_market(symbol)
    df = fetch_kline(symbol=symbol, end_date=end_date, bars=bars)
    if df is None or df.empty:
        raise ValueError(f"未能获取到 {symbol} 的有效数据")
    cfg = config or AnalyzerConfig()
    card = run_full_analysis(df, symbol=symbol, config=cfg, market=market)
    card.symbol_name = get_stock_name(symbol)
    card.market = market
    return card
```

Agent 使用：`from core import analyze; card = analyze("600802")`

### Step 4: 更新所有 import 路径

**替换规则**（共 58 处）：

| 旧 import | 新 import | 涉及文件 |
|-----------|-----------|---------|
| `src.analyzer.base` | `core.types` | 10 个文件 |
| `src.analyzer.scorer` | `core.analyzer.scorer` | 4 个文件 |
| `src.analyzer.{structure,platform,...}` | `core.analyzer.{...}` | scorer.py |
| `src.data.fetcher` | `core.data.fetcher` | 5 个文件 |
| `src.report.charger` | `core.report.chart` | 3 个文件 |
| `src.report.printer` | `core.report.printer` | 1 个文件 |
| `src.utils.helpers` | `core.utils.helpers` | 7 个文件 |
| `server.*` | `web.*` | 16 处 |
| `"server.app:app"` (uvicorn) | `"web.app:app"` | serve.py |

### Step 5: 简化 web/services/analysis.py

- 删除 `_serialize()`、`scorecard_to_dict()`、`extract_grades()` — 已移到 `core/serializer.py`
- `analyze_stock()` 改为调用 `from core import analyze`
- 仅保留 `analyze_batch_sync()`（数据库写入逻辑属于 web 层）

### Step 6: 简化 scripts/

各脚本中的重复模式（fetch → analyze → set name/market）替换为 `from core import analyze`。

### Step 7: 重写 README.md

结构：

```
# zq-trade — 六维K线分析引擎

## 快速开始
### Agent 调用（Python API）
### CLI 单股分析
### 启动 Web 标注系统

## 六维分析体系（DL/PT/LK/TY/DN/SF 说明表格）

## 项目结构
  core/    — 分析引擎（面向 Agent）
  web/     — Web 标注系统（面向人类）
  scripts/ — CLI 与批量工具

## API 参考
  core.analyze() / core.ScoreCard / core.scorecard_to_dict()

## 开发规范
  - core/ 禁止导入 web.*
  - 新增维度 checklist
  - 导入规范 / 命名规范
```

---

## 关键约束

- **单向依赖**: `core/` 不允许导入 `web.*`、`fastapi`、`sqlite3`；`web/` 可导入 `core.*`
- **路径兼容**: `core/` 与 `src/` 同级替换，`Path(__file__).parent.parent.parent / 'data'` 路径计算不受影响
- **新增维度 checklist**: types.py 添加 Result → analyzer/ 新建文件 → scorer.py 集成 → report 更新 → serializer 更新 → TRADING_RULES.md 更新

---

## 验证方式

1. **核心引擎**: `python -c "from core import analyze, ScoreCard; card = analyze('600802'); print(card.position_size)"`
2. **CLI 脚本**: `python scripts/analyze.py analyze 600802 --chart`
3. **Web 系统**: `python scripts/serve.py` → 访问 http://localhost:8000 → 创建批次 → 分析 → 标注 → 导出
4. **import 检查**: `grep -r "from src\." .` 和 `grep -r "from server\." .` 均应返回空
