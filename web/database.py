"""SQLite 数据库管理"""
import sqlite3
from contextlib import contextmanager
from web.config import DB_PATH


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
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
    """创建所有表"""
    with get_db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS batches (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            total_count INTEGER NOT NULL DEFAULT 0,
            completed_count INTEGER NOT NULL DEFAULT 0,
            labeled_count INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS stocks (
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
            FOREIGN KEY (batch_id) REFERENCES batches(id) ON DELETE CASCADE
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

        CREATE INDEX IF NOT EXISTS idx_stocks_batch_id ON stocks(batch_id);
        CREATE INDEX IF NOT EXISTS idx_labels_stock_id ON labels(stock_id);
        """)
