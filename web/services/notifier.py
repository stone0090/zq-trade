"""通知服务 — 可插拔架构"""
import json
import logging
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional
from pathlib import Path

import requests

from web.database import get_db

logger = logging.getLogger(__name__)


class Notifier(ABC):
    """通知器基类"""

    @abstractmethod
    def send_text(self, text: str) -> bool:
        ...

    @abstractmethod
    def send_image(self, image_path: str, caption: str = "") -> bool:
        ...

    @abstractmethod
    def send_card(self, title: str, fields: dict, image_url: str = "") -> bool:
        ...

    def _log(self, channel: str, ntype: str, title: str, content: str,
             status: str, error: str = ""):
        """记录通知到数据库"""
        try:
            import uuid
            with get_db() as conn:
                conn.execute(
                    """INSERT INTO notifications
                       (id, channel, type, title, content, status, error_message, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (str(uuid.uuid4()), channel, ntype, title, content,
                     status, error, datetime.now().isoformat())
                )
        except Exception as e:
            logger.warning(f"记录通知日志失败: {e}")


class FeishuWebhookNotifier(Notifier):
    """飞书自定义机器人 Webhook"""

    def __init__(self, webhook_url: str):
        self.webhook_url = webhook_url

    def send_text(self, text: str) -> bool:
        payload = {
            "msg_type": "text",
            "content": {"text": text}
        }
        ok, err = self._post(payload)
        self._log("feishu", "text", text[:50], text, "sent" if ok else "failed", err)
        return ok

    def send_image(self, image_path: str, caption: str = "") -> bool:
        # 飞书 webhook 不支持直接发图片文件，改用富文本带描述
        text = caption or f"图片: {Path(image_path).name}"
        return self.send_text(text)

    def send_card(self, title: str, fields: dict, image_url: str = "") -> bool:
        elements = []
        # 构建字段内容
        field_lines = [f"**{k}**: {v}" for k, v in fields.items()]
        elements.append({
            "tag": "div",
            "text": {"tag": "lark_md", "content": "\n".join(field_lines)}
        })

        payload = {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {"tag": "plain_text", "content": title},
                    "template": "blue"
                },
                "elements": elements
            }
        }
        ok, err = self._post(payload)
        self._log("feishu", "card", title, json.dumps(fields, ensure_ascii=False),
                   "sent" if ok else "failed", err)
        return ok

    def _post(self, payload: dict) -> tuple[bool, str]:
        try:
            resp = requests.post(
                self.webhook_url,
                json=payload,
                timeout=10,
                headers={"Content-Type": "application/json"}
            )
            data = resp.json()
            if data.get("code") == 0 or data.get("StatusCode") == 0:
                return True, ""
            err = data.get("msg") or data.get("StatusMessage") or str(data)
            return False, err
        except Exception as e:
            return False, str(e)


def get_notifier() -> Optional[Notifier]:
    """根据系统设置获取当前配置的通知器"""
    try:
        with get_db() as conn:
            channel_row = conn.execute(
                "SELECT value FROM settings WHERE key='notify_channel'"
            ).fetchone()
            if not channel_row or not channel_row['value']:
                return None

            channel = channel_row['value']

            if channel == 'feishu':
                url_row = conn.execute(
                    "SELECT value FROM settings WHERE key='feishu_webhook_url'"
                ).fetchone()
                if url_row and url_row['value']:
                    return FeishuWebhookNotifier(url_row['value'])
            return None
    except Exception as e:
        logger.warning(f"获取通知器失败: {e}")
        return None
