# ZQ-Trade 架构设计文档

## 背景

项目有两部分核心能力：

1. **K线分析引擎** - 核心能力，未来面向 Agent 使用
2. **Web 标注系统** - 面向人类，用于后台管理和人工标注

原始项目结构将分析引擎放在 `src/`，Web 系统放在 `server/`，语义不够清晰，且缺少面向 Agent 的统一 API。

## 设计目标

- 分析引擎作为独立包，Agent 一行 `from core import analyze` 即可调用
- Web 系统作为上层应用，单向依赖 core，不可反向依赖
- 命名语义化，目录结构反映架构意图
- 保留 git 历史

## 架构决策

### 1. 目录重命名

| 原路径 | 新路径 | 理由 |
|--------|--------|------|
| `src/` | `core/` | "core" 明确表达核心引擎定位 |
| `server/` | `web/` | "web" 更准确描述 HTTP 服务 + UI 的本质 |
| `src/analyzer/base.py` | `core/types.py` | 符合 Python 惯例，"types" 表明只含数据定义 |
| `src/report/charger.py` | `core/report/chart.py` | 修正命名歧义 |
| 根目录脚本 | `scripts/` | 集中管理 CLI 工具 |
| `doc/` | `docs/` | 符合开源社区惯例 |

### 2. 依赖方向

```
scripts/ ──> core/
web/     ──> core/
core/    ──> (无外部依赖)
```

**严格规则**: `core` 不可以 import `web` 或 `scripts` 中的任何内容。

### 3. Public API 设计 (`core/__init__.py`)

面向 Agent 的一站式接口：

```python
from core import analyze
card = analyze("600802")
```

`analyze()` 封装了完整流程：市场检测 -> 数据获取 -> 六维分析 -> 元数据填充 -> 返回 ScoreCard。

同时导出底层 API 供需要精细控制的场景使用：

```python
from core import fetch_kline, run_full_analysis, AnalyzerConfig, ScoreCard
```

### 4. 序列化层提取 (`core/serializer.py`)

原先 `scorecard_to_dict()`、`extract_grades()`、`_serialize()` 定义在 `web/services/analysis.py` 中。这些是引擎能力而非 Web 专属逻辑，因此提取到 `core/serializer.py`，Web 层直接调用。

### 5. 六维分析流程

```
PT(粗算) -> DL -> PT(修正DL) -> LK -> SF -> TY -> DN
```

每个维度对应 `core/analyzer/` 下的独立模块，由 `scorer.py` 统一编排。

分析结果统一存储在 `ScoreCard` dataclass 中（定义在 `core/types.py`）。

## 新增功能规范

| 场景 | 操作 |
|------|------|
| 新增分析维度 | `core/analyzer/` 新增模块 + `core/types.py` 定义结果类 + `scorer.py` 集成 |
| 新增 Web 页面 | `web/routes/` 新增路由，通过 `from core import ...` 调用引擎 |
| 新增 CLI 工具 | `scripts/` 新增脚本，使用 `from core import analyze` |
| 新增数据类型 | 统一在 `core/types.py` 定义 |
| 新增序列化逻辑 | 统一在 `core/serializer.py` 定义 |
