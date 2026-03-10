"""测试从旧版 batches 结构迁移到新版 tags 结构"""
import os
import sys
import uuid
import sqlite3
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from web import config
config.DB_PATH = config.DATA_DIR / 'test_migrate.db'

# 确保干净
if config.DB_PATH.exists():
    os.remove(str(config.DB_PATH))
bak = config.DB_PATH.with_suffix('.db.bak')
if bak.exists():
    os.remove(str(bak))

# 1. 创建旧版数据库
conn = sqlite3.connect(str(config.DB_PATH))
conn.execute("PRAGMA foreign_keys=ON")
conn.executescript("""
    CREATE TABLE batches (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        created_at TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        total_count INTEGER NOT NULL DEFAULT 0,
        completed_count INTEGER NOT NULL DEFAULT 0,
        labeled_count INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE stocks (
        id TEXT PRIMARY KEY,
        batch_id TEXT NOT NULL,
        symbol TEXT NOT NULL,
        symbol_name TEXT NOT NULL DEFAULT '',
        market TEXT NOT NULL DEFAULT 'cn',
        end_date TEXT,
        status TEXT NOT NULL DEFAULT 'pending',
        error_message TEXT,
        score_card_json TEXT,
        chart_path TEXT,
        dl_grade TEXT, pt_grade TEXT, lk_grade TEXT,
        sf_grade TEXT, ty_grade TEXT, dn_grade TEXT,
        conclusion TEXT, position_size TEXT,
        created_at TEXT NOT NULL,
        analyzed_at TEXT,
        FOREIGN KEY (batch_id) REFERENCES batches(id) ON DELETE CASCADE
    );
    CREATE TABLE labels (
        id TEXT PRIMARY KEY,
        stock_id TEXT NOT NULL UNIQUE,
        dl_grade TEXT, dl_note TEXT DEFAULT '',
        pt_grade TEXT, pt_note TEXT DEFAULT '',
        lk_grade TEXT, lk_note TEXT DEFAULT '',
        sf_grade TEXT, sf_note TEXT DEFAULT '',
        ty_grade TEXT, ty_note TEXT DEFAULT '',
        dn_grade TEXT, dn_note TEXT DEFAULT '',
        verdict TEXT, reason TEXT DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY (stock_id) REFERENCES stocks(id) ON DELETE CASCADE
    );
""")

now = datetime.now().isoformat()

# 插入测试数据：2个batch，其中有一个重复 symbol
batch1_id = str(uuid.uuid4())
batch2_id = str(uuid.uuid4())
conn.execute("INSERT INTO batches VALUES (?,?,?,?,?,?,?)",
             (batch1_id, '3月扫描', now, 'completed', 2, 2, 1))
conn.execute("INSERT INTO batches VALUES (?,?,?,?,?,?,?)",
             (batch2_id, '4月扫描', now, 'completed', 1, 1, 0))

# batch1: 600000, 600001
stock1_id = str(uuid.uuid4())
stock2_id = str(uuid.uuid4())
conn.execute("INSERT INTO stocks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
             (stock1_id, batch1_id, '600000', '浦发银行', 'cn', '2024-03-01',
              'completed', None, None, None, 'A', 'B', 'S', '1st', 'A', 'B',
              '结论1', '1R', now, '2024-03-01T12:00:00'))
conn.execute("INSERT INTO stocks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
             (stock2_id, batch1_id, '600001', '邯郸钢铁', 'cn', '2024-03-01',
              'completed', None, None, None, 'B', 'C', 'A', '2nd', 'B', 'C',
              '结论2', '0.5R', now, now))

# batch2: 600000 (重复！)
stock3_id = str(uuid.uuid4())
conn.execute("INSERT INTO stocks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
             (stock3_id, batch2_id, '600000', '浦发银行', 'cn', '2024-04-01',
              'completed', None, None, None, 'S', 'A', 'S', '1st', 'S', 'A',
              '结论3-更新', '1R', now, '2024-04-01T12:00:00'))

# stock1 有 label
label1_id = str(uuid.uuid4())
conn.execute("INSERT INTO labels VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
             (label1_id, stock1_id, 'A', '备注DL', 'B', '', 'S', '', '1st', '',
              'A', '', 'B', '', '1R做', '理由1', now, now))

conn.commit()
conn.close()

print("旧版数据库已创建，开始迁移...")

# 2. 运行迁移
from web.database import init_db, get_db
init_db()

# 3. 验证
with get_db() as conn:
    # 检查表结构
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    table_names = [t['name'] for t in tables]
    print('Tables:', table_names)
    assert 'batches' not in table_names, "batches should be removed"
    assert 'tags' in table_names, "tags should exist"
    assert 'stock_tags' in table_names, "stock_tags should exist"
    print('Table structure: OK')

    # 检查 stocks 无 batch_id
    cols = conn.execute('PRAGMA table_info(stocks)').fetchall()
    col_names = [c['name'] for c in cols]
    assert 'batch_id' not in col_names, "batch_id should be removed from stocks"
    print('No batch_id: OK')

    # 检查 symbol 唯一（600000 只保留一条）
    stocks = conn.execute("SELECT * FROM stocks ORDER BY symbol").fetchall()
    symbols = [s['symbol'] for s in stocks]
    print('Stocks:', symbols)
    assert len(symbols) == len(set(symbols)), "symbols should be unique"
    assert '600000' in symbols
    assert '600001' in symbols
    print('Symbol uniqueness: OK')

    # 600000 应该保留 analyzed_at 最新的（stock3_id, 4月的数据）
    s600000 = conn.execute("SELECT * FROM stocks WHERE symbol='600000'").fetchone()
    assert s600000['dl_grade'] == 'S', f"Expected S grade, got {s600000['dl_grade']}"
    print('Kept newest analyzed_at: OK')

    # 检查 label 迁移（label1 原属于 stock1(600000 old)，应该迁移到新的 600000）
    labels = conn.execute("SELECT * FROM labels").fetchall()
    print(f'Labels count: {len(labels)}')
    # label 可能被保留也可能被删除（因为新stock已没有label）
    # stock3 原来没有 label, stock1 有 label -> label 应该迁移到保留的 stock
    if labels:
        label = labels[0]
        label_stock = conn.execute(
            "SELECT symbol FROM stocks WHERE id=?", (label['stock_id'],)
        ).fetchone()
        print(f'Label belongs to: {label_stock["symbol"]}')

    # 检查 tags
    tags = conn.execute("SELECT * FROM tags ORDER BY name").fetchall()
    tag_names = [t['name'] for t in tags]
    print('Tags:', tag_names)
    assert '3月扫描' in tag_names, "batch1 name should become tag"
    assert '4月扫描' in tag_names, "batch2 name should become tag"
    print('Tags created from batches: OK')

    # 检查 stock_tags
    st = conn.execute("""
        SELECT s.symbol, t.name FROM stock_tags stg
        JOIN stocks s ON s.id = stg.stock_id
        JOIN tags t ON t.id = stg.tag_id
        ORDER BY s.symbol, t.name
    """).fetchall()
    print('Stock-tag links:', [(r['symbol'], r['name']) for r in st])
    # 600000 应该关联到 3月扫描 和 4月扫描
    links_600000 = [r['name'] for r in st if r['symbol'] == '600000']
    assert '3月扫描' in links_600000, "600000 should be linked to 3月扫描"
    assert '4月扫描' in links_600000, "600000 should be linked to 4月扫描"
    print('Multi-tag links for duplicate symbol: OK')

    # 检查备份
    assert bak.exists(), "backup should exist"
    print('Backup created: OK')

# 清理
os.remove(str(config.DB_PATH))
if bak.exists():
    os.remove(str(bak))
print('\nMigration test: ALL PASS')
