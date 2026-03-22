"""集成测试: 验证全部8个增强功能"""
import os
import sys
sys.path.insert(0, '.')

from web import config
config.DB_PATH = config.DATA_DIR / 'test_enhancements.db'
if config.DB_PATH.exists():
    os.remove(str(config.DB_PATH))

from web.database import init_db
init_db()

from fastapi.testclient import TestClient
from web.app import app

client = TestClient(app)

passed = 0
failed = 0

def check(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"[PASS] {name} {detail}")
    else:
        failed += 1
        print(f"[FAIL] {name} {detail}")


# 1. 页面路由测试
for p in ['/', '/dashboard', '/universe', '/trading', '/scheduler', '/settings']:
    r = client.get(p)
    check(f"GET {p}", r.status_code == 200, f"-> {r.status_code}")

# /monitor 重定向到 /universe
r = client.get('/monitor', follow_redirects=False)
check("GET /monitor redirect", r.status_code == 302, "-> 302")

# 2. 品种库统计
r = client.get('/api/universe/stats')
check("Universe stats", r.status_code == 200)

# 3. 添加品种 - 自动市场检测 + 自动分析（用不太可能已存在的代码）
r = client.post('/api/universe/add', json={'symbol': 'ZZZ_TEST', 'watch_status': 'pending'})
data = r.json()
check("Add stock ZZZ_TEST", r.status_code == 200 and data.get('ok'))
msg = data.get('message', '')
check("Auto-analysis triggered", '分析' in msg or '已添加' in msg, f"msg={msg}")

# 4. 添加A股 - 自动检测市场
r = client.post('/api/universe/add', json={'symbol': '600519', 'watch_status': 'idle'})
check("Add stock 600519", r.status_code == 200)

# 验证市场自动检测
r = client.get('/api/universe/stocks?watch_status=idle')
stocks = r.json()['items']
cn_stocks = [s for s in stocks if s['symbol'] == '600519']
if cn_stocks:
    check("Auto-detect CN market", cn_stocks[0]['market'] == 'cn', f"market={cn_stocks[0]['market']}")
else:
    check("Auto-detect CN market", False, "stock not found")

# 5. 生成模拟数据
r = client.post('/api/universe/mock-data')
check("Mock data creation", r.status_code == 200 and r.json()['ok'], r.json().get('message',''))

# 6. 验证交易数据
r = client.get('/api/trading/summary')
check("Trading summary", r.status_code == 200)
summary = r.json()
check("Has open positions", summary['open_count'] > 0, f"open={summary['open_count']}")
check("Has trade history", summary['account']['total_trades'] > 0, f"trades={summary['account']['total_trades']}")

r = client.get('/api/trading/positions')
positions = r.json()
check("Positions list", len(positions) >= 2, f"count={len(positions)}")

r = client.get('/api/trading/history')
history = r.json()
check("History list", len(history) >= 4, f"count={len(history)}")

# 验证有止损和止盈的订单
stop_loss_orders = [h for h in history if h.get('close_reason') == 'stop_loss']
take_profit_orders = [h for h in history if h.get('close_reason') == 'take_profit']
check("Has stop_loss orders", len(stop_loss_orders) >= 1, f"count={len(stop_loss_orders)}")
check("Has take_profit orders", len(take_profit_orders) >= 1, f"count={len(take_profit_orders)}")

# 7. 验证删除功能
# 先找一个 removed 的品种
r = client.get('/api/universe/stocks?watch_status=removed')
removed = r.json()['items']
if removed:
    sid = removed[0]['id']
    sym = removed[0]['symbol']
    r = client.delete(f'/api/universe/{sid}')
    check(f"Delete removed stock {sym}", r.status_code == 200)
else:
    # 先移除一个，再删除
    r = client.get('/api/universe/stocks?watch_status=idle')
    idle_stocks = r.json()['items']
    if idle_stocks:
        sid = idle_stocks[0]['id']
        client.post(f'/api/universe/{sid}/remove')
        r = client.delete(f'/api/universe/{sid}')
        check("Delete after remove", r.status_code == 200)

# 不能删除非 removed 状态的
r = client.get('/api/universe/stocks?watch_status=holding')
holding = r.json()['items']
if holding:
    r = client.delete(f'/api/universe/{holding[0]["id"]}')
    check("Cannot delete holding stock", r.status_code == 400)

# 8. 调度器 - 修改任务周期
r = client.put('/api/scheduler/jobs/focus_monitor/trigger', json={
    'trigger': 'interval', 'trigger_args': {'minutes': 10}
})
check("Update focus_monitor trigger", r.status_code == 200)

# 验证修改生效
r = client.get('/api/scheduler/jobs')
jobs = r.json()
fm = [j for j in jobs if j['id'] == 'focus_monitor']
check("Trigger updated to 10min", fm and fm[0]['trigger_args']['minutes'] == 10)

# 9. 日志分页
r = client.get('/api/scheduler/logs?page=1&page_size=5')
check("Logs pagination", r.status_code == 200)
data = r.json()
check("Logs has pagination fields", all(k in data for k in ['items', 'total', 'page', 'total_pages']))

# 10. 品种库合并验证 - 通过 universe API 也能查询 watching/focused/holding
for ws in ['watching', 'focused', 'holding']:
    r = client.get(f'/api/universe/stocks?watch_status={ws}')
    check(f"Universe lists {ws} stocks", r.status_code == 200)

# 汇总
print()
print(f"=== Results: {passed} passed, {failed} failed ===")
if failed > 0:
    sys.exit(1)
