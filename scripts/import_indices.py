"""批量导入指数成分股到品种库

导入: 中证500、标普500、恒生科技指数
状态: idle（在库中）
导入后自动触发分析
"""
import uuid
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime
from web.database import get_db


def detect_market(symbol: str) -> str:
    if symbol.isdigit():
        return 'cn' if len(symbol) >= 6 else 'hk'
    return 'us'


def get_csi500():
    """获取中证500成分股"""
    import akshare as ak
    print("[中证500] 正在获取成分股列表...")
    df = ak.index_stock_cons_csindex(symbol='000905')
    codes = df.iloc[:, 4].tolist()
    print(f"[中证500] 获取到 {len(codes)} 只成分股")
    return codes


def get_sp500():
    """获取标普500成分股"""
    import requests
    import pandas as pd
    from io import StringIO
    print("[标普500] 正在获取成分股列表...")
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    resp = requests.get('https://en.wikipedia.org/wiki/List_of_S%26P_500_companies', headers=headers)
    tables = pd.read_html(StringIO(resp.text))
    symbols = tables[0]['Symbol'].tolist()
    # yfinance 兼容: BRK.B -> BRK-B
    symbols = [s.replace('.', '-') for s in symbols]
    print(f"[标普500] 获取到 {len(symbols)} 只成分股")
    return symbols


def get_hstech():
    """获取恒生科技指数成分股"""
    print("[恒生科技] 使用预定义成分股列表...")
    codes = [
        '00020', '00241', '00268', '00285', '00300',
        '00700', '00780', '00981', '00992', '01024',
        '01211', '01347', '01698', '01810', '02015',
        '02382', '03690', '03888', '06618', '06690',
        '09618', '09626', '09660', '09863', '09866',
        '09868', '09888', '09961', '09988', '09999',
    ]
    print(f"[恒生科技] 共 {len(codes)} 只成分股")
    return codes


def batch_insert(symbols, tag_name):
    """批量插入股票到品种库"""
    now = datetime.now().isoformat()
    inserted = 0
    skipped = 0
    new_ids = []

    with get_db() as conn:
        # 创建标签
        tag_row = conn.execute("SELECT id FROM tags WHERE name=?", (tag_name,)).fetchone()
        if tag_row:
            tag_id = tag_row['id']
        else:
            tag_id = str(uuid.uuid4())
            conn.execute("INSERT INTO tags (id, name, created_at) VALUES (?,?,?)",
                         (tag_id, tag_name, now))

        for sym in symbols:
            sym = sym.strip()
            if not sym:
                continue
            market = detect_market(sym)

            # 检查是否已存在（无 end_date 的记录）
            existing = conn.execute(
                "SELECT id, watch_status FROM stocks WHERE symbol=? AND COALESCE(end_date,'')=''",
                (sym,)
            ).fetchone()

            if existing:
                stock_id = existing['id']
                ws = existing['watch_status'] or 'none'
                # 如果状态是 none 或 removed，更新为 idle
                if ws in ('none', 'removed'):
                    conn.execute(
                        "UPDATE stocks SET watch_status='idle', market=?, source_type='index', updated_at=? WHERE id=?",
                        (market, now, stock_id)
                    )
                    new_ids.append(stock_id)
                    inserted += 1
                else:
                    skipped += 1
            else:
                stock_id = str(uuid.uuid4())
                conn.execute(
                    """INSERT INTO stocks (id, symbol, market, watch_status, source_type, created_at, updated_at)
                       VALUES (?, ?, ?, 'idle', 'index', ?, ?)""",
                    (stock_id, sym, market, now, now)
                )
                new_ids.append(stock_id)
                inserted += 1

            # 关联标签
            existing_link = conn.execute(
                "SELECT 1 FROM stock_tags WHERE stock_id=? AND tag_id=?",
                (stock_id, tag_id)
            ).fetchone()
            if not existing_link:
                conn.execute("INSERT INTO stock_tags (stock_id, tag_id) VALUES (?,?)",
                             (stock_id, tag_id))

    print(f"  新增 {inserted} 只，跳过 {skipped} 只（已在品种库中）")
    return new_ids


def main():
    all_new_ids = []

    # 1. 中证500
    try:
        csi500 = get_csi500()
        ids = batch_insert(csi500, '中证500')
        all_new_ids.extend(ids)
    except Exception as e:
        print(f"[中证500] 失败: {e}")

    time.sleep(2)  # 数据源间隔

    # 2. 标普500
    try:
        sp500 = get_sp500()
        ids = batch_insert(sp500, '标普500')
        all_new_ids.extend(ids)
    except Exception as e:
        print(f"[标普500] 失败: {e}")

    time.sleep(2)  # 数据源间隔

    # 3. 恒生科技
    try:
        hstech = get_hstech()
        ids = batch_insert(hstech, '恒生科技')
        all_new_ids.extend(ids)
    except Exception as e:
        print(f"[恒生科技] 失败: {e}")

    print(f"\n共新增 {len(all_new_ids)} 只股票到品种库（状态: 在库中）")

    if all_new_ids:
        print(f"\n请在 Web 界面通过标签（中证500/标普500/恒生科技）触发批量分析，")
        print(f"或运行以下命令触发分析（注意: 1000+只股票分析需要较长时间）:")
        print(f"  curl -X POST http://localhost:8000/api/stocks/analyze -H 'Content-Type: application/json' -d '{{\"stock_ids\": [...]}}'")

        # 直接触发分析
        answer = input("\n是否立即触发分析？(y/n): ").strip().lower()
        if answer == 'y':
            import threading
            from web.services.analysis import analyze_stocks_sync
            from web.config import DB_PATH, CHARTS_DIR

            print(f"开始分析 {len(all_new_ids)} 只股票，这可能需要很长时间...")
            analyze_stocks_sync(all_new_ids, str(DB_PATH), str(CHARTS_DIR))
            print("分析完成！")
        else:
            print("已跳过分析，请在 Web 界面手动触发。")


if __name__ == '__main__':
    main()
