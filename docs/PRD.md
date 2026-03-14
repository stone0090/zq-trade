# ZQ-Trade 半自动交易系统 PRD

> 版本: v1.0 | 更新: 2026-03-11

## 一、产品定位

将现有的六维分析标注工具升级为**半自动交易系统**，实现：品种发现 → 自动分析 → 分类监控 → 模拟下单 → 持续调优 的完整闭环。

**核心原则**：
- 人机协作：系统自动发现和分析，人工确认关键决策
- 渐进式：先模拟交易验证策略，稳定盈利后再接实盘
- 市场优先级：美股 → A股 → 外汇/期货/数字货币

---

## 二、系统架构

### 2.1 架构分层

```
┌──────────────────────────────────────────────────────────┐
│                    Web 管理界面                            │
│  仪表盘 │ 品种库 │ 监控列表 │ 模拟交易 │ 任务配置 │ 系统设置  │
├──────────────────────────────────────────────────────────┤
│                    业务服务层                              │
│  品种发现  │ 监控引擎 │ 交易引擎 │ 新闻采集 │ 通知服务      │
├──────────────────────────────────────────────────────────┤
│                    核心能力层                              │
│  六维分析引擎(core) │ 定时任务(scheduler) │ 状态机          │
├──────────────────────────────────────────────────────────┤
│                    数据/接口层                             │
│  SQLite │ 行情数据(富途/Yahoo) │ 知识星球 │ 飞书Webhook     │
└──────────────────────────────────────────────────────────┘
```

### 2.2 依赖方向

```
web/templates/  ──→  web/routes/  ──→  web/services/
                                          │
                    scripts/              ↓
                       │           core/ (六维分析引擎)
                       ↓
                    scheduler/  ──→  web/services/  ──→  core/
```

**严格规则**（沿用 architecture.md）：`core/` 不依赖 `web/`、`scheduler/` 或 `scripts/`。

### 2.3 新增目录

```
zq-trade/
├── core/               # 六维分析引擎（已有）
├── web/                # Web 管理界面（已有，扩展）
│   ├── routes/         # API 路由
│   ├── services/       # 业务逻辑
│   │   ├── analysis.py    # 六维分析服务（已有）
│   │   ├── monitor.py     # 监控引擎 (NEW)
│   │   ├── trader.py      # 模拟交易引擎 (NEW)
│   │   ├── notifier.py    # 通知服务 (NEW)
│   │   ├── zsxq.py        # 知识星球爬虫 (NEW)
│   │   └── news.py        # 新闻采集 (NEW)
│   └── templates/      # 页面模板
├── scheduler/          # 定时任务引擎 (NEW)
│   ├── __init__.py
│   ├── engine.py       # APScheduler 封装
│   └── jobs.py         # 任务定义
├── scripts/            # CLI 工具（已有）
├── docs/               # 文档（已有）
└── data/               # 数据（已有）
```

---

## 三、股票生命周期（状态机）

### 3.1 状态定义

| 状态 | 说明 | 扫描频率 |
|------|------|---------|
| **待入库** (pending) | 从知识星球/手动添加，等待人工确认 | 不扫描 |
| **在库中** (idle) | 已确认入库，暂不满足关注条件 | 每日扫描1次 |
| **关注中** (watching) | 形态部分满足，值得持续跟踪 | 每1小时 |
| **重点** (focused) | 形态接近成熟，随时可能触发 | 每5分钟 |
| **持仓中** (holding) | 已触发交易条件，模拟下单持仓中 | 实时监控 |
| **已移除** (removed) | 形态严重走坏，不再跟踪 | 不扫描 |

### 3.2 状态转移规则

```
                    ┌─────────────────────────────┐
                    │         待入库               │
                    │  (知识星球/手动添加)           │
                    └──────────┬──────────────────┘
                               │ 人工确认
                               ▼
              ┌─────────────  在库中  ◄──────────────┐
              │            (品种池)                    │
              │               │                       │
              │               │ 每日扫描               │ 平仓
              │               │ 满足条件               │
              │               ▼                       │
              │            关注中  ◄────┐              │
              │          (每1h监控)     │ TY走坏       │
              │               │        │              │
              │               │ 形态    │              │
              │               │ 改善    │              │
              │               ▼        │              │
              │             重点 ──────┘              │
              │          (每5min监控)                   │
              │               │                       │
              │               │ 六维达标               │
              │               │ 模拟下单               │
              │               ▼                       │
              │            持仓中 ─────────────────────┘
              │          (止损/止盈/手动平仓)
              │
              │ 形态严重走坏
              ▼
            已移除
         (不再跟踪)
```

**完整转移路径**：

| 从 | 到 | 触发条件 |
|----|-----|---------|
| 待入库 → 在库中 | 人工确认通过 |
| 在库中 → 关注中 | 每日扫描发现形态部分满足（DL=S，其他维度部分达标） |
| 在库中 → 已移除 | 形态严重恶化或人工移除 |
| 关注中 → 重点 | 形态改善：DL=S / PT≥B / LK≥B / SF=1st，TY/DN待触发 |
| 关注中 → 在库中 | 形态退化但不严重（LK/PT 从B降到C），回到品种池等下次日扫描重新评估 |
| 重点 → 关注中 | TY走坏（挤压区被打破），但 LK/PT/SF 仍达标 |
| 重点 → 持仓中 | 六维全部达标，模拟下单 |
| 持仓中 → 在库中 | 平仓（止损/止盈/手动），回到品种池等待下一次机会 |

### 3.3 重点列表触发条件（下单）

同时满足以下条件时模拟下单：
- DL = S
- PT ≥ A
- LK ≥ A
- SF = 1st
- TY ≥ B（挤压收敛形成）
- DN ≥ B（出现突破信号）
- 最新价位于合理入场区间

---

## 四、模块详细设计

### 4.1 定时任务引擎

**技术方案**: APScheduler 集成到 FastAPI 生命周期

```python
# scheduler/engine.py
from apscheduler.schedulers.asyncio import AsyncIOScheduler

scheduler = AsyncIOScheduler()

# 任务类型
JOBS = {
    "daily_scan":    {"trigger": "cron", "hour": 8, "minute": 0},   # 每日品种扫描
    "hourly_watch":  {"trigger": "interval", "minutes": 60},         # 关注中
    "focus_monitor": {"trigger": "interval", "minutes": 5},          # 重点列表
    "kline_refresh": {"trigger": "interval", "minutes": 60},         # K线数据刷新
    "news_collect":  {"trigger": "interval", "minutes": 30},         # 新闻采集
}
```

**管理页面功能**：
- 查看所有任务及其状态（运行中/暂停/异常）
- 手动触发/暂停/恢复任务
- 查看任务执行历史和日志
- 修改任务调度参数（cron表达式/间隔时间）

### 4.2 通知服务

**可插拔架构**：

```python
# web/services/notifier.py
class Notifier(ABC):
    def send_text(self, text: str) -> bool: ...
    def send_image(self, image_path: str, caption: str) -> bool: ...
    def send_card(self, title: str, fields: dict) -> bool: ...

class FeishuWebhookNotifier(Notifier):
    """飞书自定义机器人 Webhook（P0 优先实现）"""

class TelegramNotifier(Notifier):
    """Telegram Bot API（可选）"""
```

**通知场景**：

| 场景 | 内容 | 优先级 |
|------|------|--------|
| 候选确认 | K线图 + 六维评分 + 基本面概览 | 每日推送 |
| 状态变更 | "600802 从扫描升级到重点" | 即时推送 |
| 交易信号 | "600802 DN触发，模拟买入 @ 25.30，止损 24.50" | 即时推送 |
| 异动告警 | "600802 相关新闻：XXX，价格异动 +3.5%" | 即时推送 |
| 日报 | 今日监控概览、持仓盈亏 | 每日收盘后 |

### 4.3 品种库（知识星球集成）

**数据流**：
```
知识星球帖子 → 解析提取股票代码 → 基本面概览 → 六维分析 → 推送确认 → 入库
```

**知识星球爬虫**：
- 认证：Bearer Token（手动从浏览器获取，配置到系统设置）
- 接口：`GET /v2/groups/{group_id}/topics`（获取最新帖子）
- 解析：正则提取股票代码（$AAPL、600802 等），NLP 提取交易方向
- 频率：每日定时爬取

**基本面概览**（简要）：
- 行业分类、市值规模
- PE / PB / ROE 等核心指标
- 近期重大事件（财报日、拆股、并购等）
- 数据源：Yahoo Finance API（免费）

### 4.4 监控引擎

**核心逻辑**：

```python
# web/services/monitor.py

async def scan_focused_list():
    """重点列表扫描（每5分钟）"""
    for stock in get_stocks_by_status('focused'):
        # 1. 获取最新价格
        price = await get_latest_price(stock.symbol)
        # 2. 检查是否突破 PT 位（DN 触发）
        if check_dn_trigger(stock, price):
            await execute_paper_trade(stock, price)
            await notify("交易信号", ...)
        # 3. 检查 TY 是否走坏
        if check_ty_broken(stock):
            await downgrade_to_watching(stock)
            await notify("状态降级", ...)

async def scan_watching_list():
    """关注中监控（每1小时）"""
    for stock in get_stocks_by_status('watching'):
        # 1. 刷新K线数据，重新六维分析
        card = await refresh_analysis(stock)
        # 2. 检查是否升级到重点
        if meets_focused_criteria(card):
            await upgrade_to_focused(stock)
        # 3. 检查是否形态走坏
        if is_deteriorated(card):
            await remove_stock(stock)
        # 4. 持仓止损调整
        if has_position(stock):
            await adjust_stop_loss(stock)
```

### 4.5 模拟交易

**数据模型**：

```sql
-- 模拟交易订单
CREATE TABLE paper_orders (
    id TEXT PRIMARY KEY,
    stock_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL,        -- 'long' / 'short'
    order_type TEXT NOT NULL,       -- 'market' / 'limit'
    price REAL NOT NULL,            -- 成交价
    quantity INTEGER NOT NULL,
    stop_loss REAL,                 -- 止损价
    take_profit REAL,               -- 止盈价
    status TEXT NOT NULL,           -- 'open' / 'closed' / 'cancelled'
    open_time TEXT NOT NULL,
    close_time TEXT,
    close_price REAL,
    close_reason TEXT,              -- 'stop_loss' / 'take_profit' / 'manual' / 'signal'
    pnl REAL,                       -- 盈亏金额
    pnl_pct REAL,                   -- 盈亏比例
    created_at TEXT NOT NULL
);

-- 账户状态
CREATE TABLE paper_account (
    id TEXT PRIMARY KEY,
    initial_capital REAL NOT NULL DEFAULT 100000,
    current_capital REAL NOT NULL DEFAULT 100000,
    total_trades INTEGER DEFAULT 0,
    win_trades INTEGER DEFAULT 0,
    total_pnl REAL DEFAULT 0,
    max_drawdown REAL DEFAULT 0,
    updated_at TEXT NOT NULL
);
```

**仓位管理**：
- 初始虚拟资金：10万美元
- 单笔风险：1R = 账户的 1%（1000 美元）
- 止损幅度决定仓位大小：`quantity = 1R / (entry_price - stop_loss)`
- 最大同时持仓：5 只

### 4.6 新闻采集

**数据源**（优先级排序）：
1. Yahoo Finance News API（免费，覆盖美股）
2. 新浪财经（A股新闻）
3. Google News RSS

**异动检测规则**：
- 盘中价格波动 > 3%（相对前收盘）
- 成交量异常放大 > 2倍均量
- 关键词匹配：财报、并购、回购、拆股、FDA、制裁等

---

## 五、页面原型设计

### 5.1 导航结构

扩展现有顶部导航栏：

```
┌──────────────────────────────────────────────────────────────────┐
│  ZQ-Trade   [仪表盘] [标注管理] [品种库] [监控列表] [模拟交易] [任务] [设置] │
└──────────────────────────────────────────────────────────────────┘

路由:
  仪表盘        /dashboard          (NEW) 系统概览
  标注管理      /                   (已有) 股票列表 + 标注详情
  品种库        /universe           (NEW) 待入库 + 在库中
  监控列表      /monitor            (NEW) 重点 + 关注中 + 持仓中
  模拟交易      /trading            (NEW) 持仓 + 历史订单 + 盈亏
  定时任务      /scheduler          (NEW) 任务配置和日志
  系统设置      /settings           (NEW) API密钥、通知配置
```

### 5.2 仪表盘 /dashboard

```
┌─────────────────────────────────────────────────────────────┐
│  ZQ-Trade                                    仪表盘          │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐       │
│  │ 重点关注  │ │  关注中   │ │ 当前持仓  │ │ 今日盈亏  │       │
│  │    12    │ │    35    │ │   3/5    │ │ +$1,250  │       │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘       │
│                                                             │
│  ┌─────────────────────────┐ ┌─────────────────────────┐   │
│  │ 最近信号                 │ │ 最近通知                 │   │
│  │ 09:35 AAPL DN触发 买入  │ │ 10:00 日报已推送         │   │
│  │ 09:20 TSLA 升级到重点   │ │ 09:35 AAPL交易信号已推送 │   │
│  │ 08:00 每日扫描完成 +3新  │ │ 09:20 TSLA状态变更已推送 │   │
│  └─────────────────────────┘ └─────────────────────────┘   │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ 累计业绩                                             │   │
│  │ 总交易: 45  |  胜率: 62%  |  盈亏比: 2.1  |  最大回撤: 8%│
│  │ [收益曲线图]                                         │   │
│  └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

### 5.3 品种库 /universe

```
┌─────────────────────────────────────────────────────────────┐
│  品种库管理                            [+ 手动添加] [同步星球] │
├─────────────────────────────────────────────────────────────┤
│  [待入库 (5)] [在库中 (18)] [已移除 (3)]                      │
├─────────────────────────────────────────────────────────────┤
│  ══ 待入库 Tab ══                                            │
│  来源筛选: [全部▾] [知识星球▾] [手动添加▾]                     │
│                                                             │
│  □ 代码    名称         市场  来源       基本面      六维概览      操作          │
│  ─────────────────────────────────────────────────────────────│
│  □ NVDA   英伟达        美股  知识星球   PE:45 市值:3.2T  S/A/A/1st/B/待定  [✓确认] [✗拒绝]│
│  □ AAPL   苹果          美股  知识星球   PE:32 市值:3.8T  S/S/A/1st/C/待定  [✓确认] [✗拒绝]│
│  ─────────────────────────────────────────────────────────────│
│  点击行展开: K线图 + 六维分析详情 + 基本面概览                     │
│                                                             │
│  ══ 在库中 Tab ══                                            │
│  (已确认入库但暂不满足关注条件，等待每日扫描重新评估)              │
│                                                             │
│  □ 代码    名称         市场   六维概览            上次评估     操作          │
│  ─────────────────────────────────────────────────────────────│
│  □ 600802  福建水泥     A股   S/A/B/2nd/A/S     03-10 08:00  [→扫描] [移除]│
│  □ META    Meta        美股   S/B/C/2nd/C/待定  03-10 08:00  [→扫描] [移除]│
│  ─────────────────────────────────────────────────────────────│
│  ══ 已移除 Tab ══                                            │
│  (形态走坏被移除的历史记录，可恢复)                              │
└─────────────────────────────────────────────────────────────┘
```

### 5.4 监控列表 /monitor

```
┌─────────────────────────────────────────────────────────────┐
│  监控列表                                                    │
├─────────────────────────────────────────────────────────────┤
│  [重点关注 (12)] [关注中 (35)] [持仓中 (3)]  搜索: [________]│
├─────────────────────────────────────────────────────────────┤
│  ══ 重点关注 Tab（每5分钟自动刷新）══                          │
│                                                             │
│  代码    名称      最新价    PT位    距PT    DL PT LK SF TY DN 上次扫描   异动│
│  ─────────────────────────────────────────────────────────────│
│  AAPL   苹果      178.50   180.2  -0.9%  S  A  A  1st B  待定  2min前    │
│  TSLA   特斯拉    245.80   248.0  -0.9%  S  S  A  1st A  待定  2min前    │
│  NVDA   英伟达    890.20   895.0  -0.5%  S  A  A  1st S  待定  2min前  ⚡│
│                                                             │
│  [⚡] = 有新闻异动，点击查看详情                                │
│  点击行展开: 实时K线 + 六维详情 + 相关新闻 + 操作按钮            │
│  操作: [降级到扫描] [移除] [手动下单]                           │
├─────────────────────────────────────────────────────────────┤
│  ══ 关注中 Tab（每1小时自动刷新）══                            │
│                                                             │
│  代码    名称      最新价   DL PT LK SF TY DN  上次分析       趋势│
│  ─────────────────────────────────────────────────────────────│
│  AMZN   亚马逊    185.30  S  B  B  2nd C  待定  30min前      →│
│  META   Meta     510.20  S  A  B  1st C  待定  30min前      ↑│
│  ─────────────────────────────────────────────────────────────│
│  [↑] = 近期改善趋势    [→] = 横盘    [↓] = 近期恶化           │
│  操作: [升级到重点] [移除]                                     │
└─────────────────────────────────────────────────────────────┘
```

### 5.5 模拟交易 /trading

```
┌─────────────────────────────────────────────────────────────┐
│  模拟交易                                                    │
├─────────────────────────────────────────────────────────────┤
│  账户概览                                                    │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐       │
│  │ 初始资金  │ │ 当前净值  │ │ 总盈亏    │ │ 收益率   │       │
│  │ $100,000 │ │ $112,500 │ │ +$12,500 │ │ +12.5%  │       │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘       │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐       │
│  │ 总交易数  │ │ 胜率     │ │ 盈亏比    │ │ 最大回撤  │       │
│  │    45    │ │   62%   │ │   2.1    │ │   8.2%  │       │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘       │
├─────────────────────────────────────────────────────────────┤
│  [当前持仓 (3)] [历史订单 (42)]                               │
│                                                             │
│  ══ 当前持仓 ══                                              │
│  代码   方向  数量   开仓价   现价    止损    盈亏      盈亏%    │
│  AAPL  多    55   178.50  180.20  175.00  +$93.50  +0.95%  │
│  TSLA  多    20   245.80  248.30  240.00  +$50.00  +1.02%  │
│  NVDA  多    5    890.20  885.50  870.00  -$23.50  -0.53%  │
│                                                             │
│  ══ 历史订单 ══                                              │
│  日期        代码   方向  开仓价  平仓价  盈亏      原因       │
│  03-10 14:30 META  多   510.20 518.50 +$830    止盈        │
│  03-09 10:15 AMZN  多   185.30 183.20 -$420    止损        │
├─────────────────────────────────────────────────────────────┤
│  [收益曲线图]                                                │
└─────────────────────────────────────────────────────────────┘
```

### 5.6 定时任务 /scheduler

```
┌─────────────────────────────────────────────────────────────┐
│  定时任务管理                                                 │
├─────────────────────────────────────────────────────────────┤
│  任务名称        调度规则          上次执行      下次执行     状态  操作│
│  ─────────────────────────────────────────────────────────────│
│  每日品种扫描    每天 08:00       03-11 08:00  03-12 08:00  ✅运行  [暂停] [立即执行]│
│  重点列表监控    每5分钟          03-11 14:25  03-11 14:30  ✅运行  [暂停] [立即执行]│
│  关注中监控      每1小时          03-11 14:00  03-11 15:00  ✅运行  [暂停] [立即执行]│
│  K线数据刷新     每1小时          03-11 14:00  03-11 15:00  ✅运行  [暂停] [立即执行]│
│  新闻采集        每30分钟         03-11 14:00  03-11 14:30  ✅运行  [暂停] [立即执行]│
│  日报推送        每天 16:30       03-10 16:30  03-11 16:30  ✅运行  [暂停] [立即执行]│
│  ─────────────────────────────────────────────────────────────│
│  点击任务行展开: 最近执行日志（时间、耗时、结果、错误信息）        │
├─────────────────────────────────────────────────────────────┤
│  执行日志（最近50条）                                         │
│  时间           任务            耗时    结果                   │
│  14:25:03      重点列表监控     2.3s   扫描12只，无信号触发     │
│  14:00:05      K线数据刷新      15.2s  刷新47只K线数据         │
│  14:00:03      关注中监控       8.7s   扫描35只，META升级到重点 │
└─────────────────────────────────────────────────────────────┘
```

### 5.7 系统设置 /settings

```
┌─────────────────────────────────────────────────────────────┐
│  系统设置                                                    │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ── 通知配置 ──                                              │
│  通知通道:  [飞书Webhook ▾]                                   │
│  Webhook URL: [https://open.feishu.cn/open-apis/bot/v2/hook/xxx]│
│  [测试发送]  状态: ✅ 已连通                                   │
│                                                             │
│  ── 知识星球 ──                                              │
│  Access Token: [xxxxxxxxxxxxxxxx]                            │
│  星球 Group ID: [xxxxxxxx]                                   │
│  Token 状态: ✅ 有效（过期时间: 2026-05-15）                    │
│  [测试连接]                                                   │
│                                                             │
│  ── 行情数据 ──                                              │
│  美股数据源: [Yahoo Finance ▾]                                │
│  富途 OpenD: [未配置]  IP: [127.0.0.1] 端口: [11111]          │
│  [测试连接]                                                   │
│                                                             │
│  ── 交易参数 ──                                              │
│  初始资金: [$100,000]                                        │
│  单笔风险(1R): [1%]                                          │
│  最大持仓数: [5]                                              │
│  默认止损方式: [ATR止损 ▾]  ATR倍数: [2.0]                     │
│                                                             │
│  [保存设置]                                                   │
└─────────────────────────────────────────────────────────────┘
```

---

## 六、实现优先级

### P0 — 基础设施（先做）

| 模块 | 内容 | 说明 |
|------|------|------|
| **导航改造** | base.html 扩展顶部导航栏 | 从单页升级为多页应用 |
| **定时任务引擎** | APScheduler + 任务配置页 | 所有自动化的基础 |
| **通知服务** | 飞书 Webhook + 可插拔架构 | 所有推送的基础 |
| **系统设置** | 配置管理页 + settings 表 | API密钥、通知配置等 |

### P1 — 核心业务流程

| 模块 | 内容 | 说明 |
|------|------|------|
| **品种库** | 知识星球爬虫 + 候选确认流程 | 品种来源 |
| **状态机** | stocks 表扩展状态字段 + 流转逻辑 | 核心业务模型 |
| **监控引擎** | 重点/关注中监控 + 条件判断 | 自动化监控 |
| **监控页面** | 重点/关注中双Tab + 实时状态 | 操作界面 |
| **行情刷新** | K线定时更新 + 最新价获取 | 数据驱动 |

### P2 — 交易与分析

| 模块 | 内容 | 说明 |
|------|------|------|
| **模拟交易** | 虚拟下单/止损/持仓管理 | 策略验证 |
| **仪表盘** | 系统概览 + 业绩统计 | 全局视图 |
| **新闻采集** | Yahoo/新浪新闻 + 异动检测 | 辅助决策 |
| **基本面概览** | 行业/市值/PE等核心指标 | 辅助筛选 |

### P3 — 后期扩展

| 模块 | 内容 | 说明 |
|------|------|------|
| **实盘对接** | 富途(美股)、国金(A股) | 需模拟交易验证策略后 |
| **品种自动筛选** | 全市场扫描 + 初筛规则 | 减少对知识星球依赖 |
| **OpenClaw交互** | IM 双向交互（方案B） | 提升操作便捷性 |
| **更多市场** | 外汇、期货、数字货币 | 市场扩展 |

---

## 七、数据库扩展

### 新增表

```sql
-- 系统配置（键值对）
CREATE TABLE settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- 品种来源记录
CREATE TABLE stock_sources (
    id TEXT PRIMARY KEY,
    stock_id TEXT NOT NULL,
    source_type TEXT NOT NULL,     -- 'zsxq' / 'manual' / 'auto_scan'
    source_ref TEXT,               -- 知识星球帖子ID等
    raw_content TEXT,              -- 原始内容
    created_at TEXT NOT NULL,
    FOREIGN KEY (stock_id) REFERENCES stocks(id) ON DELETE CASCADE
);

-- 模拟交易订单
CREATE TABLE paper_orders (
    id TEXT PRIMARY KEY,
    stock_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL,
    order_type TEXT NOT NULL,
    price REAL NOT NULL,
    quantity INTEGER NOT NULL,
    stop_loss REAL,
    take_profit REAL,
    status TEXT NOT NULL DEFAULT 'open',
    open_time TEXT NOT NULL,
    close_time TEXT,
    close_price REAL,
    close_reason TEXT,
    pnl REAL,
    pnl_pct REAL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (stock_id) REFERENCES stocks(id)
);

-- 模拟账户
CREATE TABLE paper_account (
    id TEXT PRIMARY KEY,
    initial_capital REAL NOT NULL DEFAULT 100000,
    current_capital REAL NOT NULL DEFAULT 100000,
    total_trades INTEGER DEFAULT 0,
    win_trades INTEGER DEFAULT 0,
    total_pnl REAL DEFAULT 0,
    max_drawdown REAL DEFAULT 0,
    updated_at TEXT NOT NULL
);

-- 定时任务日志
CREATE TABLE job_logs (
    id TEXT PRIMARY KEY,
    job_name TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    duration_ms INTEGER,
    status TEXT NOT NULL,          -- 'success' / 'error'
    result_summary TEXT,
    error_message TEXT
);

-- 新闻记录
CREATE TABLE stock_news (
    id TEXT PRIMARY KEY,
    stock_id TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT,
    source TEXT,
    url TEXT,
    is_alert INTEGER DEFAULT 0,   -- 是否触发异动
    published_at TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (stock_id) REFERENCES stocks(id) ON DELETE CASCADE
);

-- 通知记录
CREATE TABLE notifications (
    id TEXT PRIMARY KEY,
    channel TEXT NOT NULL,         -- 'feishu' / 'telegram'
    type TEXT NOT NULL,            -- 'candidate' / 'signal' / 'alert' / 'report'
    title TEXT NOT NULL,
    content TEXT,
    status TEXT NOT NULL,          -- 'sent' / 'failed'
    error_message TEXT,
    created_at TEXT NOT NULL
);
```

### stocks 表扩展

```sql
-- 新增字段
ALTER TABLE stocks ADD COLUMN watch_status TEXT DEFAULT 'none';
    -- 'none' / 'pending' / 'idle' / 'watching' / 'focused' / 'holding' / 'removed'
ALTER TABLE stocks ADD COLUMN source_type TEXT DEFAULT 'manual';
    -- 'manual' / 'zsxq' / 'auto_scan'
ALTER TABLE stocks ADD COLUMN last_price REAL;
ALTER TABLE stocks ADD COLUMN last_price_time TEXT;
ALTER TABLE stocks ADD COLUMN fundamental_json TEXT;  -- 基本面数据缓存
ALTER TABLE stocks ADD COLUMN news_alert INTEGER DEFAULT 0;
```

---

## 八、技术选型

| 组件 | 技术 | 理由 |
|------|------|------|
| Web框架 | FastAPI（已有） | 异步支持好，适合定时任务集成 |
| 定时任务 | APScheduler | 轻量，支持 asyncio，持久化到 SQLite |
| 数据库 | SQLite（已有） | 本地部署，单用户，够用 |
| 通知 | 飞书 Webhook | 零成本，支持富文本卡片和图片 |
| 美股行情 | Yahoo Finance (yfinance) | 免费，小时线够用 |
| 美股实时价 | Yahoo Finance / 富途 OpenD | 5min轮询用 |
| 新闻 | Yahoo Finance News | 免费，英文美股新闻 |
| 知识星球 | 逆向 API (requests) | 已调研完成 |
| 前端 | Tailwind CSS + 原生 JS（已有） | 保持现有技术栈一致 |
