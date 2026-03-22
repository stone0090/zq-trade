# Agent Rules

## 工作流程铁律

1. **先确认需求，再写代码** — 所有维度的文档逐一确认完毕后，用户明确说"可以开始写代码了"，才能动代码。不要在确认过程中提前修改任何代码。
2. **文档确认流程** — 按维度顺序逐个确认，每个维度展示当前规则，等用户反馈。用户说"继续下一个"才进入下一维度。全部确认完后等用户指令再动手。
3. **不要自作主张** — 用户没有明确要求写代码时，绝对不写代码。即使看到偏差或问题，也只汇报，不自行修复。

## 项目概述

六维打分开仓分析工具 — 基于小时K线的股票交易条件分析系统。
六个维度: DL(独立结构) → PT(平台位) → LK(轮廓质量) → SF(释放级别) → TY(统一区间) → DN(动能)

## 项目架构

```
core/                           # 核心分析引擎 (Agent API)
  __init__.py                   # Public API: from core import analyze
  types.py                      # 数据结构 (ScoreCard/GradeScore/AnalyzerConfig)
  serializer.py                 # ScoreCard 序列化
  analyzer/                     # 六维分析器 (scorer/structure/platform/contour/squeeze/momentum/release)
  data/fetcher.py               # 数据获取 (Sina/akshare/yfinance + 本地缓存)
  report/printer.py             # 终端报告
  report/chart.py               # K线图表 (matplotlib)
  utils/helpers.py              # 工具函数

web/                            # 标注管理系统 (FastAPI + Jinja2 + SQLite)
  app.py / config.py / database.py / models.py
  routes/ (batches/stocks/labels)
  services/ (analysis/export)
  templates/ / static/

scripts/                        # CLI 工具脚本
  analyze.py                    # 单股分析
  serve.py                      # 启动 Web 服务
  batch_analyze.py / batch_charts.py / batch_compare.py

data/labeled_cases.csv          # 人工标注案例集
docs/TRADING_RULES.md           # 交易规则文档
docs/architecture.md            # 架构设计文档
```

**依赖规则**: web/scripts 可以 import core，core 不可以 import web。

## Python 环境

- 使用 venv 虚拟环境，Python 解释器路径: `venv\Scripts\python.exe`
- 所有 python 命令必须用 `venv\Scripts\python.exe` 执行，不要用系统 python

## 常用命令

```bash
# 单股分析
python scripts/analyze.py analyze <代码>
python scripts/analyze.py analyze <代码> --end 2026-03-07
python scripts/analyze.py analyze <代码> --chart

# 批量对比 / 批量图表
python scripts/batch_compare.py
python scripts/batch_charts.py

# 启动 Web 标注系统
python scripts/serve.py
```

## 数据获取铁律

- **所有品种（A股、港股、美股）统一使用小时K线分析，禁止降级为日K线。**
- 取数失败时直接报错，不得用日线缓存替代小时线返回。日K线与小时K线粒度完全不同，降级会导致分析结果错误。
- `core/data/fetcher.py` 中不允许出现"取数失败 → 回退日线"的兜底逻辑。

## 评分体系

- **DL/PT/LK/TY/DN**: GradeScore 枚举 (S=4, A=3, B=2, C=1)
- **SF**: ReleaseLevel 枚举 (1st=最优, 2nd=需等回踩, 3rd=需等新结构)
- **通用规则**: 非S评分必须附带文字原因说明
- 阈值参数集中在 `AnalyzerConfig` dataclass (core/types.py)

## Git 推送注意事项

- 远程仓库: `https://github.com/stone0090/zq-trade.git`
- **推送前务必检查 git proxy 配置**: 如果推送失败报 `OpenSSL SSL_connect: Connection was reset`，先尝试去掉代理直连：
  ```bash
  git config --global --unset http.proxy
  git config --global --unset https.proxy
  git push origin master
  ```
- 本机 127.0.0.1:7890 有代理服务，但代理开关状态不确定。直连 github.com 通常可用，代理反而可能干扰 SSL 握手。
- 如果直连也失败，再尝试加代理：
  ```bash
  git config --global http.proxy http://127.0.0.1:7890
  git config --global https.proxy http://127.0.0.1:7890
  ```
