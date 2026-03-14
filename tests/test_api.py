"""测试 API 端点"""
import os
import sys
import json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 使用测试数据库
from web import config
config.DB_PATH = config.DATA_DIR / 'test_api.db'
if config.DB_PATH.exists():
    os.remove(str(config.DB_PATH))

# 先手动初始化数据库（TestClient 的 startup 事件可能不可靠）
from web.database import init_db
init_db()

from fastapi.testclient import TestClient
from web.app import app

client = TestClient(app)

# 1. 创建标签
print("--- 创建标签 ---")
r = client.post("/api/tags", json={"name": "测试标签A"})
assert r.status_code == 200, f"Create tag failed: {r.text}"
tag_a = r.json()
print(f"  Created: {tag_a['name']} (id={tag_a['id'][:8]}...)")

r = client.post("/api/tags", json={"name": "测试标签B"})
assert r.status_code == 200
tag_b = r.json()
print(f"  Created: {tag_b['name']}")

# 重复创建应失败
r = client.post("/api/tags", json={"name": "测试标签A"})
assert r.status_code == 400
print("  Duplicate tag rejected: OK")

# 2. 获取标签列表
r = client.get("/api/tags")
assert r.status_code == 200
tags = r.json()
assert len(tags) == 2
print(f"  Tags list: {[t['name'] for t in tags]}")

# 3. 导入股票
print("\n--- 导入股票 ---")
r = client.post("/api/stocks/import", json={
    "symbols": ["600000", "600001", "00700"],
    "end_date": "2024-03-01",
    "tags": ["测试标签A"]
})
assert r.status_code == 200
result = r.json()
assert result['imported'] == 3
assert result['skipped'] == 0
print(f"  Imported: {result['imported']}, Skipped: {result['skipped']}")

# 重复导入
r = client.post("/api/stocks/import", json={
    "symbols": ["600000", "600002"],
    "tags": ["测试标签B"]
})
assert r.status_code == 200
result = r.json()
assert result['imported'] == 1  # 600002 是新的
assert result['skipped'] == 1  # 600000 已存在
print(f"  Re-import: Imported={result['imported']}, Skipped={result['skipped']}")

# 4. 获取股票列表
print("\n--- 股票列表 ---")
r = client.get("/api/stocks")
assert r.status_code == 200
data = r.json()
stocks = data['items']
assert data['total'] == 4
assert len(stocks) == 4
print(f"  Total stocks: {data['total']}")
print(f"  Symbols: {[s['symbol'] for s in stocks]}")

# 按标签筛选
r = client.get("/api/stocks?tag=测试标签A")
assert r.status_code == 200
filtered = r.json()['items']
symbols = [s['symbol'] for s in filtered]
assert '600000' in symbols
assert '600001' in symbols
assert '00700' in symbols
print(f"  Tag filter '测试标签A': {symbols}")

r = client.get("/api/stocks?tag=测试标签B")
filtered_b = r.json()['items']
symbols_b = [s['symbol'] for s in filtered_b]
assert '600000' in symbols_b  # 600000 导入时关联了 B
assert '600002' in symbols_b
print(f"  Tag filter '测试标签B': {symbols_b}")

# 5. 检查 tags 字段
stock_600000 = next(s for s in stocks if s['symbol'] == '600000')
assert '测试标签A' in stock_600000['tags']
assert '测试标签B' in stock_600000['tags']
print(f"  600000 tags: {stock_600000['tags']}")

# 6. 获取股票详情
print("\n--- 股票详情 ---")
r = client.get(f"/api/stocks/{stock_600000['id']}")
assert r.status_code == 200
detail = r.json()
assert detail['symbol'] == '600000'
assert '测试标签A' in detail['tags']
print(f"  Detail: {detail['symbol']}, tags={detail['tags']}")

# 7. 更新股票
print("\n--- 更新股票 ---")
r = client.put(f"/api/stocks/{stock_600000['id']}", json={
    "end_date": "2024-06-01",
    "tags": ["测试标签A", "新标签"]
})
assert r.status_code == 200
print("  Updated end_date and tags")

# 验证更新
r = client.get(f"/api/stocks/{stock_600000['id']}")
detail = r.json()
assert detail['end_date'] == '2024-06-01'
assert '测试标签A' in detail['tags']
assert '新标签' in detail['tags']
assert '测试标签B' not in detail['tags']  # 替换式更新
print(f"  Verified: end_date={detail['end_date']}, tags={detail['tags']}")

# 8. 保存标注
print("\n--- 保存标注 ---")
r = client.put(f"/api/stocks/{stock_600000['id']}/label", json={
    "dl_grade": "S", "dl_note": "强势",
    "pt_grade": "A", "verdict": "1R做", "reason": "测试理由"
})
assert r.status_code == 200
print("  Label saved")

# 获取标注
r = client.get(f"/api/stocks/{stock_600000['id']}/label")
assert r.status_code == 200
label = r.json()
assert label['dl_grade'] == 'S'
assert label['verdict'] == '1R做'
print(f"  Label: dl={label['dl_grade']}, verdict={label['verdict']}")

# 9. 标注状态筛选
r = client.get("/api/stocks?label_status=labeled")
labeled = r.json()['items']
assert len(labeled) == 1
assert labeled[0]['symbol'] == '600000'
print(f"  Labeled filter: {[s['symbol'] for s in labeled]}")

# 10. 导出 CSV
print("\n--- 导出 CSV ---")
r = client.get("/api/export")
assert r.status_code == 200
assert 'text/csv' in r.headers['content-type']
csv_content = r.text
assert '600000' in csv_content
print(f"  Export all: {len(csv_content)} bytes")

# 按标签导出
r = client.get(f"/api/export?tag_id={tag_a['id']}")
assert r.status_code == 200
print(f"  Export by tag: {len(r.text)} bytes")

# 11. 重命名标签
print("\n--- 标签操作 ---")
r = client.put(f"/api/tags/{tag_a['id']}", json={"name": "重命名标签"})
assert r.status_code == 200
assert r.json()['name'] == '重命名标签'
print("  Rename tag: OK")

# 12. 删除标签（不删股票）
r = client.delete(f"/api/tags/{tag_b['id']}")
assert r.status_code == 200
print("  Delete tag: OK")

# 股票应该还在
r = client.get("/api/stocks")
assert r.json()['total'] == 4
print("  Stocks still exist after tag delete: OK")

# 13. 删除股票
print("\n--- 删除股票 ---")
stock_00700 = next(s for s in r.json()['items'] if s['symbol'] == '00700')
r = client.delete(f"/api/stocks/{stock_00700['id']}")
assert r.status_code == 200
r = client.get("/api/stocks")
assert r.json()['total'] == 3
print("  Delete stock: OK, remaining: 3")

# 14. 批量更新
print("\n--- 批量更新 ---")
# 先拿到所有股票
r = client.get("/api/stocks")
all_s = r.json()['items']
ids_to_update = [s['id'] for s in all_s[:2]]

# 批量修改日期
r = client.post("/api/stocks/batch-update", json={
    "stock_ids": ids_to_update,
    "end_date": "2024-12-31"
})
assert r.status_code == 200
print(f"  Batch update date: OK")

# 验证日期
for sid in ids_to_update:
    r = client.get(f"/api/stocks/{sid}")
    assert r.json()['end_date'] == '2024-12-31'
print(f"  Date verified for {len(ids_to_update)} stocks")

# 批量修改标签 (replace 模式)
r = client.post("/api/stocks/batch-update", json={
    "stock_ids": ids_to_update,
    "tags": ["批量标签"],
    "tag_mode": "replace"
})
assert r.status_code == 200
for sid in ids_to_update:
    r = client.get(f"/api/stocks/{sid}")
    assert "批量标签" in r.json()['tags']
print(f"  Batch tag replace: OK")

# 批量追加标签 (add 模式)
r = client.post("/api/stocks/batch-update", json={
    "stock_ids": ids_to_update,
    "tags": ["追加标签"],
    "tag_mode": "add"
})
assert r.status_code == 200
for sid in ids_to_update:
    r = client.get(f"/api/stocks/{sid}")
    tags = r.json()['tags']
    assert "批量标签" in tags  # 原有的还在
    assert "追加标签" in tags  # 新追加的
print(f"  Batch tag add: OK")

# 15. 分析进度端点
print("\n--- 分析进度 ---")
r = client.get("/api/stocks/progress")
assert r.status_code == 200
p = r.json()
assert p['running'] == False
print(f"  Progress: running={p['running']}")

# 15. 页面路由
print("\n--- 页面路由 ---")
r = client.get("/")
assert r.status_code == 200
assert '股票列表' in r.text
print("  Homepage: OK")

r = client.get(f"/stocks/{stock_600000['id']}")
assert r.status_code == 200
assert '600000' in r.text
print("  Stock detail page: OK")

# 清理
os.remove(str(config.DB_PATH))
print("\nAPI tests: ALL PASS")
