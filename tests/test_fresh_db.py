"""测试全新数据库初始化"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from web import config
config.DB_PATH = config.DATA_DIR / 'test_new.db'

# 确保干净
if config.DB_PATH.exists():
    os.remove(str(config.DB_PATH))

from web.database import init_db, get_db

init_db()

with get_db() as conn:
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    print('Tables:', [t['name'] for t in tables])

    info = conn.execute('PRAGMA table_info(stocks)').fetchall()
    print('Stocks cols:', [i['name'] for i in info])

    info2 = conn.execute('PRAGMA table_info(stock_tags)').fetchall()
    print('stock_tags cols:', [i['name'] for i in info2])

    info3 = conn.execute('PRAGMA table_info(tags)').fetchall()
    print('tags cols:', [i['name'] for i in info3])

    has_b = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='batches'"
    ).fetchone()
    print('Has batches table:', has_b is not None)

    # 验证 stocks 表无 batch_id 列
    stock_cols = [i['name'] for i in info]
    assert 'batch_id' not in stock_cols, "stocks should not have batch_id"
    assert 'symbol' in stock_cols, "stocks should have symbol"
    print('No batch_id in stocks: OK')

os.remove(str(config.DB_PATH))
print('\nFresh DB init: ALL PASS')
