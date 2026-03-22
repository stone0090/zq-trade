# Web 标注系统重构：移除批次，引入标签分组

## Context

当前标注系统采用三层结构：**批次(batches) -> 股票(stocks) -> 标注(labels)**。用户要求移除"批次"概念，将所有股票平铺在首页，并引入"标签(tags)"实现多对多分组。这样可以让同一只股票属于多个分组，同时每只股票可以独立设置截止日期和触发重新分析。

**用户确认的需求：**
- 标签与股票为多对多关系（一个股票可有多个标签）
- 股票代码全局唯一（同一 symbol 不能重复添加）

## 涉及文件

| 文件 | 操作 | 说明 |
|------|------|------|
| `web/database.py` | 修改 | 新增 tags/stock_tags 表，迁移旧数据，移除 batches 表 |
| `web/models.py` | 修改 | 删除 Batch 模型，新增 Tag/Import/Progress 模型 |
| `web/routes/tags.py` | 新建 | 标签 CRUD API |
| `web/routes/stocks.py` | 重写 | 移除 batch_id 依赖，新增导入/删除/分析端点 |
| `web/routes/labels.py` | 修改 | 移除 batch 相关逻辑，导出改用 tag 筛选 |
| `web/routes/batches.py` | 删除 | 整个文件删除 |
| `web/services/analysis.py` | 修改 | 删除 analyze_batch_sync，新增按股票列表分析 + 全局进度追踪 |
| `web/services/export.py` | 修改 | 移除 batch_id 参数，改为按 tag 或全部导出 |
| `web/app.py` | 修改 | 替换路由注册和页面路由 |
| `web/templates/stock_list.html` | 重写 | 变为首页，增加标签管理/筛选/导入功能 |
| `web/templates/stock_detail.html` | 修改 | 移除 batch_id 引用，新增修改日期/标签/重分析 |
| `web/templates/batch_list.html` | 删除 | 不再需要 |
| `scripts/serve.py` | 修改 | 历史导入改用标签而非批次 |

---

## Phase 1: 数据库 Schema 迁移

### 文件: `web/database.py`

**新表结构：**

```sql
-- stocks 表：移除 batch_id，symbol 加 UNIQUE 约束
CREATE TABLE IF NOT EXISTS stocks (
    id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL UNIQUE,
    symbol_name TEXT NOT NULL DEFAULT '',
    market TEXT NOT NULL DEFAULT 'cn',
    end_date TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    error_message TEXT,
    score_card_json TEXT,
    chart_path TEXT,
    dl_grade TEXT, pt_grade TEXT, lk_grade TEXT,
    sf_grade TEXT, ty_grade TEXT, dn_grade TEXT,
    conclusion TEXT,
    position_size TEXT,
    created_at TEXT NOT NULL,
    analyzed_at TEXT,
);

-- tags 表
CREATE TABLE IF NOT EXISTS tags (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);

-- stock_tags 多对多关联表
CREATE TABLE IF NOT EXISTS stock_tags (
    stock_id TEXT NOT NULL,
    tag_id TEXT NOT NULL,
    PRIMARY KEY (stock_id, tag_id),
    FOREIGN KEY (stock_id) REFERENCES stocks(id) ON DELETE CASCADE,
    FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
);

-- labels 表：保持不变
```

**迁移策略（在 `init_db()` 中实现）：**

1. 检测 `batches` 表是否存在（判断是否需要迁移）
2. 如果需要迁移：
   - 备份数据库文件到 `data/zqtrade.db.bak`
   - 对重复 symbol 的 stocks，保留最新 `analyzed_at` 的记录，迁移其 labels
   - 将 batches 的 `name` 转为 tags（如 "CSV导入(历史标注)" -> tag "历史导入"）
   - 将 stock 与其原 batch 对应的 tag 建立关联
   - 创建新的 stocks 表（无 batch_id，有 UNIQUE symbol）
   - 删除 batches 表
3. 创建新表（tags, stock_tags）
4. 图表路径迁移：将 `charts/web/{batch_id}/{symbol}.png` 复制到 `charts/web/{symbol}.png`

---

## Phase 2: Pydantic 模型

### 文件: `web/models.py`

**删除：** BatchCreate, BatchResponse, BatchProgress

**新增：**
```python
class StockImport(BaseModel):
    symbols: List[str]
    end_date: Optional[str] = None
    tags: Optional[List[str]] = None  # 标签名列表

class StockUpdate(BaseModel):
    end_date: Optional[str] = None
    tags: Optional[List[str]] = None  # 替换式更新

class TagCreate(BaseModel):
    name: str

class TagResponse(BaseModel):
    id: str
    name: str
    stock_count: int
    created_at: str

class AnalysisProgress(BaseModel):
    running: bool
    total: int
    completed: int
    current_symbol: Optional[str] = None

class ImportResult(BaseModel):
    imported: int  # 新增数量
    skipped: int   # 已存在跳过数量
    stock_ids: List[str]
```

**修改：** StockListItem / StockDetail 新增 `tags: List[str] = []` 字段

---

## Phase 3: 服务层

### 文件: `web/services/analysis.py`

**删除：** `analyze_batch_sync()`

**新增：** 全局进度追踪 + 按股票列表分析

```python
# 全局进度对象（替代 batch 级进度）
_analysis_progress = {
    'running': False, 'total': 0, 'completed': 0, 'current_symbol': None
}
_progress_lock = threading.Lock()

def get_progress() -> dict:
    """获取当前分析进度"""

def is_running() -> bool:
    """是否有分析在运行"""

def analyze_stocks_sync(stock_ids: list, db_path: str, chart_dir: str):
    """后台线程：逐个分析指定股票列表（替代 analyze_batch_sync）
    - 图表存储路径：charts/web/{symbol}.png（不再有 batch_id 子目录）
    - 更新全局进度对象
    - 每只股票分析完直接更新 stocks 表
    """
```

### 文件: `web/services/export.py`

**修改：** `export_batch_csv(conn, batch_id)` -> `export_csv(conn, tag_id=None)`
- tag_id 为 None 时导出全部已标注数据
- tag_id 非空时只导出该 tag 下的已标注数据
- SQL 从 `WHERE s.batch_id=?` 改为 JOIN stock_tags 或无过滤

---

## Phase 4: API 路由

### 删除: `web/routes/batches.py` (整个文件)

### 新建: `web/routes/tags.py`

```
GET    /api/tags              -> 获取所有标签（含 stock_count）
POST   /api/tags              -> 创建标签 { name }
PUT    /api/tags/{tag_id}     -> 重命名标签 { name }
DELETE /api/tags/{tag_id}     -> 删除标签（仅删关联，不删股票）
```

### 重写: `web/routes/stocks.py`

```
GET    /api/stocks                        -> 列出所有股票
       查询参数: ?tag=TAG_NAME &label_status=labeled|unlabeled &market=cn|hk|us &status=...
       返回: List[StockListItem]（含 tags 字段）

GET    /api/stocks/{stock_id}             -> 股票详情（含 tags）
GET    /api/stocks/{stock_id}/chart       -> 图表文件（不变）

POST   /api/stocks/import                 -> 导入股票
       请求: StockImport { symbols, end_date?, tags? }
       逻辑: 已存在的 symbol 跳过，新的创建
       返回: ImportResult { imported, skipped, stock_ids }

PUT    /api/stocks/{stock_id}             -> 更新股票（修改 end_date、标签）
       请求: StockUpdate { end_date?, tags? }

DELETE /api/stocks/{stock_id}             -> 删除股票（级联删标注）

POST   /api/stocks/analyze                -> 批量分析（传入 stock_ids 或 tag）
       请求: { stock_ids?: [], tag?: string }
       逻辑: 后台线程调 analyze_stocks_sync
       验证: 不能在分析运行中再次触发

POST   /api/stocks/{stock_id}/analyze     -> 单只重新分析

GET    /api/stocks/progress               -> 获取分析进度
       返回: AnalysisProgress
```

### 修改: `web/routes/labels.py`

- `upsert_label`: 移除 batch_id 相关逻辑（第75-84行 labeled_count 更新删除）
- `get_label`: 不变
- `export_csv`: 参数从 `batch_id` 改为 `tag_id`（可选），无参数则导出全部

---

## Phase 5: 前端模板 + 页面路由

### 文件: `web/app.py`

```python
# 路由注册
from web.routes import tags, stocks, labels  # 不再 import batches

# 页面路由
GET /                   -> stock_list.html   # 首页变为股票列表
GET /stocks/{stock_id}  -> stock_detail.html # 简化URL，不含 batch_id
# 删除 /batches/... 相关路由
```

### 删除: `web/templates/batch_list.html`

### 重写: `web/templates/stock_list.html` (成为首页)

**页面布局：**
```
顶栏：标题 "ZQ-Trade 六维分析标注系统"
      + "导入股票" 按钮 + "管理标签" 按钮

标签筛选区：标签 chips（点击筛选）+ "全部" chip
筛选栏：标注状态下拉 + 市场下拉 + 导出CSV按钮

分析进度条（仅在分析运行时显示，轮询 /api/stocks/progress）

股票表格：
  列: 代码 | 名称 | 市场 | 标签 | 截止日期 | DL PT LK SF TY DN | 仓位 | 结论 | 标注 | 状态
  新增"标签"列 显示 tag chips
  新增"截止日期"列

导入弹窗：
  - 股票代码输入框（多行）
  - 截止日期（可选）
  - 标签选择（多选，可新建）
  - "导入并分析" 按钮

标签管理弹窗：
  - 标签列表（名称 + 股票数量）
  - 重命名/删除操作
  - 新建标签
  - "按标签批量分析" 按钮
```

**关键交互：**
- 点击标签 chip 筛选该标签的股票
- 点击股票行跳转 `/stocks/{stock_id}`
- 导入时如果 symbol 已存在则跳过并提示
- 分析进度通过轮询 `/api/stocks/progress` 更新进度条
- 导航前将当前筛选条件存入 `sessionStorage`，返回时恢复

### 修改: `web/templates/stock_detail.html`

**变更点：**
1. URL 从 `/batches/{batch_id}/stock/{stock_id}` 改为 `/stocks/{stock_id}`
2. "返回列表" 链接改为 `/`
3. 顶部新增：标签 chips（可添加/移除）+ 截止日期显示（可编辑）
4. 新增 "重新分析" 按钮（调用 `POST /api/stocks/{stock_id}/analyze`）
5. 上一只/下一只导航：从 `sessionStorage` 读取筛选条件，加载对应股票列表
6. 移除所有 `BATCH_ID` 引用

---

## Phase 6: 脚本更新

### 文件: `scripts/serve.py`

**修改 `_import_labeled_cases()`：**
- 不再创建 batch，改为：
  1. 创建 tag "历史导入"（如果不存在）
  2. 对每条 CSV 记录：symbol 存在则跳过，不存在则创建 stock + label
  3. 新 stock 关联到 "历史导入" tag
- 检查是否已导入的逻辑：从 `SELECT FROM batches WHERE name='CSV导入(历史标注)'` 改为 `SELECT FROM tags WHERE name='历史导入'`

---

## 实现顺序

严格按 Phase 顺序实施，每个 Phase 完成后验证无报错：

1. **database.py** + **models.py** - 数据库和模型层（基础）
2. **services/analysis.py** + **services/export.py** - 服务层
3. **routes/tags.py**(新) + **routes/stocks.py**(重写) + **routes/labels.py**(改) + 删除 **routes/batches.py**
4. **app.py** + **templates/stock_list.html**(重写) + **templates/stock_detail.html**(改) + 删除 **templates/batch_list.html**
5. **scripts/serve.py** - 历史数据导入

---

## 验证方案

1. **启动测试**：`python scripts/serve.py` 无报错启动
2. **数据库迁移测试**（如果有旧数据）：
   - 确认 batches 表已删除
   - 确认旧 batch name 转为 tags
   - 确认旧数据保留且 symbol 无重复
   - 确认 labels 数据完整保留
3. **API 测试**：
   - POST /api/stocks/import 导入新股票（含标签）
   - POST /api/stocks/import 重复导入同一 symbol -> 应跳过
   - GET /api/stocks 无参数 -> 返回全部
   - GET /api/stocks?tag=xxx -> 按标签筛选
   - POST /api/tags 创建标签
   - PUT /api/stocks/{id} 修改截止日期和标签
   - POST /api/stocks/analyze 触发分析，GET /api/stocks/progress 查看进度
   - POST /api/stocks/{id}/analyze 单只重分析
   - DELETE /api/stocks/{id} 删除股票
   - GET /api/export 导出 CSV
4. **前端测试**：
   - 首页显示所有股票，标签筛选正常
   - 导入弹窗创建新股票并自动分析
   - 标签管理：新建/重命名/删除
   - 股票详情页：标注保存、快捷键、上下切换、重新分析
   - 进度条在分析时正确显示
5. **历史数据导入测试**：删除数据库，重新启动，确认 CSV 数据正确导入并关联 "历史导入" 标签
