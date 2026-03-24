"""SQLite 数据库管理"""
import sqlite3
import shutil
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from web import config


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(str(config.DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def get_db():
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """创建所有表，如果存在旧版 batches 表则自动迁移"""
    with get_db() as conn:
        # 检测是否需要迁移（旧版有 batches 表）
        has_batches = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='batches'"
        ).fetchone()

        if has_batches:
            _migrate_from_batches(conn)
            return

        # 全新安装：直接创建新表
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS stocks (
            id TEXT PRIMARY KEY,
            symbol TEXT NOT NULL,
            symbol_name TEXT NOT NULL DEFAULT '',
            market TEXT NOT NULL DEFAULT 'cn',
            end_date TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            error_message TEXT,
            score_card_json TEXT,
            chart_path TEXT,
            dl_grade TEXT,
            pt_grade TEXT,
            lk_grade TEXT,
            sf_grade TEXT,
            ty_grade TEXT,
            dn_grade TEXT,
            conclusion TEXT,
            position_size TEXT,
            watch_status TEXT DEFAULT 'none',
            source_type TEXT DEFAULT 'manual',
            last_price REAL,
            last_price_time TEXT,
            fundamental_json TEXT,
            news_alert INTEGER DEFAULT 0,
            created_at TEXT NOT NULL,
            analyzed_at TEXT,
            updated_at TEXT
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_stocks_symbol_enddate
            ON stocks(symbol, COALESCE(end_date, ''));

        CREATE TABLE IF NOT EXISTS tags (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS stock_tags (
            stock_id TEXT NOT NULL,
            tag_id TEXT NOT NULL,
            PRIMARY KEY (stock_id, tag_id),
            FOREIGN KEY (stock_id) REFERENCES stocks(id) ON DELETE CASCADE,
            FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS labels (
            id TEXT PRIMARY KEY,
            stock_id TEXT NOT NULL UNIQUE,
            dl_grade TEXT,
            dl_note TEXT DEFAULT '',
            pt_grade TEXT,
            pt_note TEXT DEFAULT '',
            lk_grade TEXT,
            lk_note TEXT DEFAULT '',
            sf_grade TEXT,
            sf_note TEXT DEFAULT '',
            ty_grade TEXT,
            ty_note TEXT DEFAULT '',
            dn_grade TEXT,
            dn_note TEXT DEFAULT '',
            verdict TEXT,
            reason TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (stock_id) REFERENCES stocks(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_labels_stock_id ON labels(stock_id);
        CREATE INDEX IF NOT EXISTS idx_stock_tags_tag ON stock_tags(tag_id);

        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS job_logs (
            id TEXT PRIMARY KEY,
            job_name TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            duration_ms INTEGER,
            status TEXT NOT NULL DEFAULT 'running',
            result_summary TEXT,
            error_message TEXT
        );

        CREATE TABLE IF NOT EXISTS notifications (
            id TEXT PRIMARY KEY,
            channel TEXT NOT NULL,
            type TEXT NOT NULL,
            title TEXT NOT NULL,
            content TEXT,
            status TEXT NOT NULL DEFAULT 'sent',
            error_message TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS stock_sources (
            id TEXT PRIMARY KEY,
            stock_id TEXT NOT NULL,
            source_type TEXT NOT NULL,
            source_ref TEXT,
            raw_content TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (stock_id) REFERENCES stocks(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS paper_orders (
            id TEXT PRIMARY KEY,
            stock_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            direction TEXT NOT NULL DEFAULT 'long',
            order_type TEXT NOT NULL DEFAULT 'market',
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

        CREATE TABLE IF NOT EXISTS paper_account (
            id TEXT PRIMARY KEY,
            initial_capital REAL NOT NULL DEFAULT 100000,
            current_capital REAL NOT NULL DEFAULT 100000,
            total_trades INTEGER DEFAULT 0,
            win_trades INTEGER DEFAULT 0,
            total_pnl REAL DEFAULT 0,
            max_drawdown REAL DEFAULT 0,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS stock_news (
            id TEXT PRIMARY KEY,
            stock_id TEXT NOT NULL,
            title TEXT NOT NULL,
            summary TEXT,
            source TEXT,
            url TEXT,
            is_alert INTEGER DEFAULT 0,
            published_at TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (stock_id) REFERENCES stocks(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_paper_orders_stock ON paper_orders(stock_id);
        CREATE INDEX IF NOT EXISTS idx_paper_orders_status ON paper_orders(status);
        CREATE INDEX IF NOT EXISTS idx_stock_news_stock ON stock_news(stock_id);
        """)

        # 增量迁移：为已有数据库添加 updated_at 列
        _ensure_updated_at_column(conn)

        # 增量迁移：symbol UNIQUE → (symbol, end_date) 联合唯一索引
        _migrate_symbol_unique_to_compound(conn)

        # 增量迁移：为 stocks 表添加 watch_status 等新字段
        _ensure_watch_status_columns(conn)

        # 任务自定义配置表
        conn.execute("""
            CREATE TABLE IF NOT EXISTS job_config (
                job_id TEXT PRIMARY KEY,
                custom_name TEXT,
                custom_description TEXT,
                sort_order INTEGER DEFAULT 0,
                paused INTEGER DEFAULT 0
            )
        """)

        # 增量迁移：为 job_config 添加 paused 列
        _ensure_column(conn, 'job_config', 'paused', 'INTEGER DEFAULT 0')


def _migrate_from_batches(conn):
    """从旧版 batches 结构迁移到新版 tags 结构。
    策略：先将所有数据读入内存，处理去重和关联，再写入新表。
    """
    print("检测到旧版数据库结构，开始迁移...")

    # 1. 备份数据库
    bak_path = config.DB_PATH.with_suffix('.db.bak')
    if not bak_path.exists():
        shutil.copy2(str(config.DB_PATH), str(bak_path))
        print(f"  已备份数据库到 {bak_path}")

    now = datetime.now().isoformat()

    # ─── 阶段一：读取所有旧数据到内存 ───

    batches = conn.execute("SELECT id, name FROM batches").fetchall()
    all_stocks = conn.execute(
        "SELECT id, batch_id, symbol, symbol_name, market, end_date, status, "
        "error_message, score_card_json, chart_path, "
        "dl_grade, pt_grade, lk_grade, sf_grade, ty_grade, dn_grade, "
        "conclusion, position_size, created_at, analyzed_at "
        "FROM stocks"
    ).fetchall()
    all_labels = conn.execute("SELECT * FROM labels").fetchall()

    # 转为 dict 方便操作
    stocks_data = [dict(s) for s in all_stocks]
    labels_data = {l['stock_id']: dict(l) for l in all_labels}

    # ─── 阶段二：在内存中处理去重和映射 ───

    # batch_id -> tag_id 映射
    batch_to_tag = {}
    tag_names_seen = {}
    for b in batches:
        tag_name = b['name']
        if tag_name in tag_names_seen:
            batch_to_tag[b['id']] = tag_names_seen[tag_name]
        else:
            tag_id = str(uuid.uuid4())
            tag_names_seen[tag_name] = tag_id
            batch_to_tag[b['id']] = tag_id

    # 按 symbol 分组，找出每个 symbol 保留哪条记录
    from collections import defaultdict
    symbol_groups = defaultdict(list)
    for s in stocks_data:
        symbol_groups[s['symbol']].append(s)

    # 保留的 stock（去重后），以及 symbol -> 关联的所有 batch_id
    kept_stocks = {}  # symbol -> stock dict
    symbol_batch_ids = defaultdict(set)  # symbol -> set of batch_ids

    for symbol, group in symbol_groups.items():
        # 收集此 symbol 在所有 batch 中的关联
        for s in group:
            symbol_batch_ids[symbol].add(s['batch_id'])

        # 保留最新 analyzed_at 的记录
        group.sort(key=lambda x: (x['analyzed_at'] or '', x['created_at'] or ''), reverse=True)
        kept_stocks[symbol] = group[0]

    # 为保留的 stock 找到 label（优先从保留 stock 自身，否则从同 symbol 的其他 stock）
    kept_labels = {}  # kept_stock_id -> label dict
    for symbol, group in symbol_groups.items():
        kept_id = kept_stocks[symbol]['id']
        # 先看保留的 stock 本身有没有 label
        if kept_id in labels_data:
            kept_labels[kept_id] = labels_data[kept_id]
        else:
            # 从同 symbol 的其他 stock 找 label
            for s in group:
                if s['id'] in labels_data:
                    label = dict(labels_data[s['id']])
                    label['stock_id'] = kept_id  # 修改指向
                    kept_labels[kept_id] = label
                    break

    # ─── 阶段三：重建数据库 ───

    # 关闭外键约束以便安全删除旧表
    conn.execute("PRAGMA foreign_keys=OFF")

    # 删除旧表
    conn.execute("DROP TABLE IF EXISTS labels")
    conn.execute("DROP TABLE IF EXISTS stocks")
    conn.execute("DROP TABLE IF EXISTS batches")

    # 创建新表
    conn.executescript("""
    CREATE TABLE stocks (
        id TEXT PRIMARY KEY,
        symbol TEXT NOT NULL,
        symbol_name TEXT NOT NULL DEFAULT '',
        market TEXT NOT NULL DEFAULT 'cn',
        end_date TEXT,
        status TEXT NOT NULL DEFAULT 'pending',
        error_message TEXT,
        score_card_json TEXT,
        chart_path TEXT,
        dl_grade TEXT,
        pt_grade TEXT,
        lk_grade TEXT,
        sf_grade TEXT,
        ty_grade TEXT,
        dn_grade TEXT,
        conclusion TEXT,
        position_size TEXT,
        created_at TEXT NOT NULL,
        analyzed_at TEXT,
        kline_end_time TEXT
    );

    CREATE UNIQUE INDEX IF NOT EXISTS idx_stocks_symbol_enddate
        ON stocks(symbol, COALESCE(end_date, ''));

    CREATE TABLE IF NOT EXISTS tags (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL UNIQUE,
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS stock_tags (
        stock_id TEXT NOT NULL,
        tag_id TEXT NOT NULL,
        PRIMARY KEY (stock_id, tag_id),
        FOREIGN KEY (stock_id) REFERENCES stocks(id) ON DELETE CASCADE,
        FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
    );

    CREATE TABLE labels (
        id TEXT PRIMARY KEY,
        stock_id TEXT NOT NULL UNIQUE,
        dl_grade TEXT,
        dl_note TEXT DEFAULT '',
        pt_grade TEXT,
        pt_note TEXT DEFAULT '',
        lk_grade TEXT,
        lk_note TEXT DEFAULT '',
        sf_grade TEXT,
        sf_note TEXT DEFAULT '',
        ty_grade TEXT,
        ty_note TEXT DEFAULT '',
        dn_grade TEXT,
        dn_note TEXT DEFAULT '',
        verdict TEXT,
        reason TEXT DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY (stock_id) REFERENCES stocks(id) ON DELETE CASCADE
    );
    """)

    # 插入 tags
    for tag_name, tag_id in tag_names_seen.items():
        conn.execute(
            "INSERT INTO tags (id, name, created_at) VALUES (?,?,?)",
            (tag_id, tag_name, now)
        )

    # 插入去重后的 stocks
    for symbol, s in kept_stocks.items():
        conn.execute(
            "INSERT INTO stocks (id, symbol, symbol_name, market, end_date, status, "
            "error_message, score_card_json, chart_path, "
            "dl_grade, pt_grade, lk_grade, sf_grade, ty_grade, dn_grade, "
            "conclusion, position_size, created_at, analyzed_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (s['id'], s['symbol'], s['symbol_name'], s['market'], s['end_date'],
             s['status'], s['error_message'], s['score_card_json'], s['chart_path'],
             s['dl_grade'], s['pt_grade'], s['lk_grade'], s['sf_grade'],
             s['ty_grade'], s['dn_grade'], s['conclusion'], s['position_size'],
             s['created_at'], s['analyzed_at'])
        )

    # 插入 stock_tags 关联
    for symbol, batch_ids in symbol_batch_ids.items():
        stock_id = kept_stocks[symbol]['id']
        for bid in batch_ids:
            tag_id = batch_to_tag.get(bid)
            if tag_id:
                conn.execute(
                    "INSERT OR IGNORE INTO stock_tags (stock_id, tag_id) VALUES (?,?)",
                    (stock_id, tag_id)
                )

    # 插入迁移后的 labels
    for stock_id, label in kept_labels.items():
        conn.execute(
            "INSERT INTO labels (id, stock_id, "
            "dl_grade, dl_note, pt_grade, pt_note, lk_grade, lk_note, "
            "sf_grade, sf_note, ty_grade, ty_note, dn_grade, dn_note, "
            "verdict, reason, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (label['id'], stock_id,
             label['dl_grade'], label['dl_note'],
             label['pt_grade'], label['pt_note'],
             label['lk_grade'], label['lk_note'],
             label['sf_grade'], label['sf_note'],
             label['ty_grade'], label['ty_note'],
             label['dn_grade'], label['dn_note'],
             label['verdict'], label['reason'],
             label['created_at'], label['updated_at'])
        )

    # 重建索引
    conn.execute("CREATE INDEX IF NOT EXISTS idx_labels_stock_id ON labels(stock_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_stock_tags_tag ON stock_tags(tag_id)")

    # 重新启用外键
    conn.execute("PRAGMA foreign_keys=ON")

    # ─── 阶段四：迁移图表文件 ───
    charts_dir = Path(config.CHARTS_DIR)
    if charts_dir.exists():
        for sub in charts_dir.iterdir():
            if sub.is_dir():
                for png in sub.glob("*.png"):
                    dest = charts_dir / png.name
                    if not dest.exists():
                        shutil.copy2(str(png), str(dest))

    # 更新 chart_path 为新路径
    stocks_with_charts = conn.execute(
        "SELECT id, symbol FROM stocks WHERE chart_path IS NOT NULL"
    ).fetchall()
    for s in stocks_with_charts:
        new_path = str(charts_dir / f"{s['symbol']}.png")
        conn.execute("UPDATE stocks SET chart_path=? WHERE id=?", (new_path, s['id']))

    print("  数据库迁移完成！")


def _ensure_updated_at_column(conn):
    """确保 stocks 表有 updated_at 列（增量迁移）"""
    cols = [r['name'] for r in conn.execute("PRAGMA table_info(stocks)").fetchall()]
    if 'updated_at' not in cols:
        conn.execute("ALTER TABLE stocks ADD COLUMN updated_at TEXT")
        # 用 analyzed_at 或 created_at 回填
        conn.execute("UPDATE stocks SET updated_at = COALESCE(analyzed_at, created_at)")


def _migrate_symbol_unique_to_compound(conn):
    """将 stocks 表从 symbol UNIQUE 迁移到 (symbol, end_date) 联合唯一索引"""
    # 检查旧表是否有 symbol UNIQUE 约束（列级约束）
    tbl_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='stocks'"
    ).fetchone()
    if not tbl_sql:
        return

    # 如果表 SQL 不含 'UNIQUE'，说明已是新结构，只需确保索引存在
    if 'UNIQUE' not in tbl_sql['sql']:
        conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_stocks_symbol_enddate
                ON stocks(symbol, COALESCE(end_date, ''))
        """)
        return

    print("迁移 stocks 表: symbol UNIQUE → (symbol, end_date) 联合唯一索引...")

    # 获取当前所有列名
    cols_info = conn.execute("PRAGMA table_info(stocks)").fetchall()
    cols = [r['name'] for r in cols_info]
    cols_str = ', '.join(cols)

    conn.execute("PRAGMA foreign_keys=OFF")

    # 创建新表（无 symbol UNIQUE）
    col_defs = []
    for r in cols_info:
        name, typ, notnull, dflt, pk = r['name'], r['type'], r['notnull'], r['dflt_value'], r['pk']
        parts = [name, typ or 'TEXT']
        if pk:
            parts.append('PRIMARY KEY')
        if notnull and not pk:
            parts.append('NOT NULL')
        if dflt is not None:
            parts.append(f'DEFAULT {dflt}')
        col_defs.append(' '.join(parts))

    create_sql = f"CREATE TABLE stocks_new ({', '.join(col_defs)})"
    conn.execute(create_sql)
    conn.execute(f"INSERT INTO stocks_new ({cols_str}) SELECT {cols_str} FROM stocks")

    # 删除旧索引（如果有）
    conn.execute("DROP INDEX IF EXISTS idx_stocks_symbol_enddate")

    conn.execute("DROP TABLE stocks")
    conn.execute("ALTER TABLE stocks_new RENAME TO stocks")

    # 创建联合唯一索引
    conn.execute("""
        CREATE UNIQUE INDEX idx_stocks_symbol_enddate
            ON stocks(symbol, COALESCE(end_date, ''))
    """)

    # 重建其他索引
    conn.execute("CREATE INDEX IF NOT EXISTS idx_labels_stock_id ON labels(stock_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_stock_tags_tag ON stock_tags(tag_id)")

    conn.execute("PRAGMA foreign_keys=ON")
    print("  迁移完成！")


def _ensure_column(conn, table: str, column: str, col_def: str):
    """通用增量迁移：确保某张表有某个列，没有则添加"""
    cols = [r['name'] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_def}")


def _ensure_watch_status_columns(conn):
    """确保 stocks 表有 watch_status/source_type/last_price 等新字段"""
    cols = [r['name'] for r in conn.execute("PRAGMA table_info(stocks)").fetchall()]
    new_cols = {
        'watch_status': "ALTER TABLE stocks ADD COLUMN watch_status TEXT DEFAULT 'none'",
        'source_type': "ALTER TABLE stocks ADD COLUMN source_type TEXT DEFAULT 'manual'",
        'last_price': "ALTER TABLE stocks ADD COLUMN last_price REAL",
        'last_price_time': "ALTER TABLE stocks ADD COLUMN last_price_time TEXT",
        'fundamental_json': "ALTER TABLE stocks ADD COLUMN fundamental_json TEXT",
        'news_alert': "ALTER TABLE stocks ADD COLUMN news_alert INTEGER DEFAULT 0",
        'kline_end_time': "ALTER TABLE stocks ADD COLUMN kline_end_time TEXT",
    }
    for col_name, alter_sql in new_cols.items():
        if col_name not in cols:
            conn.execute(alter_sql)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_stocks_watch_status ON stocks(watch_status)")
