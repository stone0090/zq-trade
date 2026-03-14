"""系统设置 API"""
import uuid
from datetime import datetime
from typing import Optional
from fastapi import APIRouter
from pydantic import BaseModel

from web.database import get_db

router = APIRouter(prefix="/api/settings", tags=["settings"])


class SettingItem(BaseModel):
    key: str
    value: str


class SettingBatch(BaseModel):
    items: list[SettingItem]


@router.get("")
def list_settings():
    """获取所有设置"""
    with get_db() as conn:
        rows = conn.execute("SELECT key, value, updated_at FROM settings ORDER BY key").fetchall()
    return [dict(r) for r in rows]


@router.get("/{key}")
def get_setting(key: str):
    """获取单个设置"""
    with get_db() as conn:
        row = conn.execute("SELECT key, value, updated_at FROM settings WHERE key=?", (key,)).fetchone()
    if not row:
        return {"key": key, "value": "", "updated_at": None}
    return dict(row)


@router.put("")
def save_settings(req: SettingBatch):
    """批量保存设置"""
    now = datetime.now().isoformat()
    with get_db() as conn:
        for item in req.items:
            existing = conn.execute("SELECT key FROM settings WHERE key=?", (item.key,)).fetchone()
            if existing:
                conn.execute(
                    "UPDATE settings SET value=?, updated_at=? WHERE key=?",
                    (item.value, now, item.key)
                )
            else:
                conn.execute(
                    "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
                    (item.key, item.value, now)
                )
    return {"ok": True, "count": len(req.items)}


@router.post("/test-notify")
def test_notify():
    """测试通知发送"""
    from web.services.notifier import get_notifier
    notifier = get_notifier()
    if not notifier:
        return {"ok": False, "error": "未配置通知通道"}
    ok = notifier.send_text("ZQ-Trade 通知测试：连接成功！")
    return {"ok": ok, "error": "" if ok else "发送失败，请检查 Webhook URL"}
