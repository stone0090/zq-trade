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
    from web.services.notifier import get_notifier, FeishuWebhookNotifier
    from web.database import get_db
    
    # 检查配置
    with get_db() as conn:
        channel_row = conn.execute(
            "SELECT value FROM settings WHERE key='notify_channel'"
        ).fetchone()
        url_row = conn.execute(
            "SELECT value FROM settings WHERE key='feishu_webhook_url'"
        ).fetchone()
    
    if not channel_row or not channel_row['value']:
        return {"ok": False, "error": "未配置通知通道，请先选择通知通道并保存设置"}
    
    if channel_row['value'] == 'feishu':
        if not url_row or not url_row['value']:
            return {"ok": False, "error": "未配置飞书 Webhook URL，请先填写并保存设置"}
        
        # 直接测试，获取详细错误
        notifier = FeishuWebhookNotifier(url_row['value'])
        ok = notifier.send_text("ZQ-Trade 通知测试：连接成功！")
        
        if not ok:
            # 从数据库获取最后一次错误记录
            with get_db() as conn:
                err_row = conn.execute(
                    """SELECT error_message FROM notifications 
                       WHERE channel='feishu' AND status='failed'
                       ORDER BY created_at DESC LIMIT 1"""
                ).fetchone()
                if err_row and err_row['error_message']:
                    return {"ok": False, "error": f"发送失败: {err_row['error_message']}"}
        
        return {"ok": ok, "error": "" if ok else "发送失败，请检查 Webhook URL 是否有效"}
    
    return {"ok": False, "error": "未知的通知通道类型"}
