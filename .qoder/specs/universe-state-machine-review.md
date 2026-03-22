# 品种库状态机逻辑修正

## Context

用户反馈品种库状态机流转逻辑"很多数据不对"。核心问题：
1. **升级条件过于复杂**：watching 条件当前要求"DL=S + 任意2个其他维度>=B"，实际应只看 DL/PT/LK 三个核心维度
2. **不应自动移除**：当前 `is_deteriorated()` 会在扫描中自动把品种移到 removed，用户要求只能手动移除
3. **focused 降级逻辑硬编码**：当前只在 TY=C 时降级，应改为基于 DL/PT/LK 条件判定

用户明确要求：
- **watching 条件**：DL=S, PT>=B, LK>=B（仅这3维）
- **focused 条件**：DL=S, PT>=A, LK>=A（仅这3维）
- **禁止自动移除**：只允许手动移除，获取不到数据的除外
- **降级逻辑**：不满足当前层级条件就降级，不移除

## 变更后状态流转图

```
idle ──SBB──→ watching ──SAA──→ focused ──六维全达标──→ holding
  ↑              │                 │                      │
  │    不满足SBB ↓     不满足SAA   ↓                      │
  │           ← idle  ┌满足SBB→ watching                  │
  │                   └不满足SBB→ idle                     │
  ← ← ← ← ← ← ← ← ← ← ← ← ← ← ← ← ← ← ← 平仓 ← ← ┘
（所有状态均可手动移除到 removed，但扫描不会自动移除）
```

## 修改文件与步骤

### 1. `web/services/state_machine.py`

**1a. VALID_TRANSITIONS (line 28-36)**
- `focused` 行增加 `'idle'`，支持 focused 直接跳降到 idle

```python
# 变更前
'focused': ['watching', 'holding', 'removed'],
# 变更后
'focused': ['watching', 'holding', 'removed', 'idle'],
```

**1b. `meets_watching_criteria()` (line 91-106)**
- 改为：DL=S, PT>=B, LK>=B

```python
def meets_watching_criteria(stock: dict) -> bool:
    """idle → watching: DL=S, PT>=B, LK>=B"""
    if stock.get('dl_grade') != 'S':
        return False
    if not _grade_gte(stock.get('pt_grade'), 'B'):
        return False
    if not _grade_gte(stock.get('lk_grade'), 'B'):
        return False
    return True
```

**1c. `meets_focused_criteria()` (line 109-121)**
- 改为：DL=S, PT>=A, LK>=A（去掉 SF 要求）

```python
def meets_focused_criteria(stock: dict) -> bool:
    """watching → focused: DL=S, PT>=A, LK>=A"""
    if stock.get('dl_grade') != 'S':
        return False
    if not _grade_gte(stock.get('pt_grade'), 'A'):
        return False
    if not _grade_gte(stock.get('lk_grade'), 'A'):
        return False
    return True
```

**1d. `is_downgraded()` (line 154-158)**
- 简化为：`not meets_watching_criteria(stock)`

**1e. `meets_order_criteria()` — 不变**（六维全达标下单条件保持）

**1f. `is_deteriorated()` — 保留函数但不在扫描中调用**

**1g. 更新模块顶部文档注释**，反映新规则

### 2. `web/services/monitor.py`

**2a. 导入 (line 10-14)**
- 移除 `is_deteriorated`, `is_downgraded` 的导入

**2b. `run_daily_scan()` (line 47-76)**
- 删除 `is_deteriorated()` → removed 分支
- 删除 `removed` 计数器
- 仅保留 `meets_watching_criteria()` → watching 升级逻辑

**2c. `run_watch_monitor()` (line 79-110)**
- 删除 `is_deteriorated()` → removed 分支
- 删除 `removed` 计数器
- 降级判定改为 `not meets_watching_criteria(grades)` → idle

**2d. `run_focus_monitor()` (line 113-148)**
- 删除硬编码的 `ty == 'C'` 检查
- 改为通用条件判定：
  ```
  if not meets_focused_criteria(grades):
      if meets_watching_criteria(grades):
          → downgrade to watching
      else:
          → downgrade to idle
  ```

### 3. `scheduler/engine.py`

**3a. `_DEFAULT_DESCRIPTIONS` (line 129-135)**
- 更新 daily_scan / watch_monitor / focus_monitor 三个任务的描述文本
- 去掉"移除"相关描述，更新条件为 SBB / SAA

### 4. UI 模板（可选）

- `web/templates/universe.html` 和 `web/templates/monitor.html`：focused 状态操作按钮增加"回库"(→idle)选项

## 验证方法

1. 启动服务后，检查品种库列表中各状态品种的 DL/PT/LK 评级是否符合对应层级条件
2. 手动执行 daily_scan 任务：idle 品种中满足 SBB 的应升级，不满足的保持 idle（无 removed）
3. 手动执行 watch_monitor 任务：watching 中满足 SAA 的升级，不满足 SBB 的降级到 idle
4. 手动执行 focus_monitor 任务：focused 中不满足 SAA 但满足 SBB 的降到 watching，不满足 SBB 的降到 idle
5. 确认"移除"按钮仍可手动移除任何状态品种
