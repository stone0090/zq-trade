"""一键启动 ZQ-Trade 标注系统"""
import sys
import os
from pathlib import Path

# 确保项目根目录在 sys.path
root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))
os.chdir(str(root))


def main():
    from web.database import init_db
    from web.config import DB_PATH

    # 初始化数据库
    print("初始化数据库...")
    init_db()
    print(f"  数据库: {DB_PATH}")

    # 导入历史标注数据（首次运行）
    _import_labeled_cases()

    # 启动服务器
    import uvicorn
    port = 8000
    print(f"\n启动服务器: http://localhost:{port}")
    print("按 Ctrl+C 停止\n")
    uvicorn.run("web.app:app", host="0.0.0.0", port=port, reload=False)


def _import_labeled_cases():
    """首次运行时导入 labeled_cases.csv"""
    import csv
    import uuid
    from datetime import datetime
    from web.database import get_db
    from web.config import LABELED_CASES_CSV

    if not LABELED_CASES_CSV.exists():
        return

    with get_db() as conn:
        # 检查是否已导入
        existing = conn.execute(
            "SELECT id FROM batches WHERE name='CSV导入(历史标注)'"
        ).fetchone()
        if existing:
            return

        print("导入历史标注数据...")

        with open(LABELED_CASES_CSV, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            cases = list(reader)

        if not cases:
            return

        batch_id = str(uuid.uuid4())
        now = datetime.now().isoformat()

        conn.execute(
            "INSERT INTO batches (id, name, created_at, status, total_count, completed_count, labeled_count) VALUES (?,?,?,?,?,?,?)",
            (batch_id, 'CSV导入(历史标注)', now, 'completed', len(cases), len(cases), len(cases))
        )

        for case in cases:
            stock_id = str(uuid.uuid4())
            label_id = str(uuid.uuid4())
            symbol = case.get('symbol', '')
            end_date = case.get('end_date', '')

            # SF 值映射
            sf_map = {'1': '1st', '2': '2nd', '3': '3rd',
                      '1st': '1st', '2nd': '2nd', '3rd': '3rd'}
            sf_val = sf_map.get(case.get('SF', ''), case.get('SF', ''))

            # 检测市场
            market = 'cn'
            if symbol.isdigit():
                if len(symbol) == 5:
                    market = 'hk'
            else:
                market = 'us'

            conn.execute("""
                INSERT INTO stocks (id, batch_id, symbol, market, end_date, status,
                    dl_grade, pt_grade, lk_grade, sf_grade, ty_grade, dn_grade,
                    conclusion, position_size,
                    created_at, analyzed_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (stock_id, batch_id, symbol, market, end_date, 'completed',
                  case.get('DL', '') or None,
                  case.get('PT', '') or None,
                  case.get('LK', '') or None,
                  sf_val or None,
                  case.get('TY', '') or None,
                  case.get('DN', '') or None,
                  case.get('reason', '') or None,
                  case.get('verdict', '') or None,
                  now, now))

            conn.execute("""
                INSERT INTO labels (
                    id, stock_id,
                    dl_grade, dl_note, pt_grade, pt_note,
                    lk_grade, lk_note, sf_grade, sf_note,
                    ty_grade, ty_note, dn_grade, dn_note,
                    verdict, reason, created_at, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                label_id, stock_id,
                case.get('DL', ''), case.get('DL_length', ''),
                case.get('PT', ''), case.get('PT_note', ''),
                case.get('LK', ''), case.get('LK_note', ''),
                sf_val, '',
                case.get('TY', ''), case.get('TY_note', ''),
                case.get('DN', ''), '',
                case.get('verdict', ''), case.get('reason', ''),
                now, now,
            ))

        print(f"  已导入 {len(cases)} 条标注记录")


if __name__ == '__main__':
    main()
