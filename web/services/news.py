"""新闻采集服务"""
import uuid
import logging
import time
from datetime import datetime
from typing import Optional

from web.database import get_db
from web.services.state_machine import get_stocks_by_watch_status
from web.services.notifier import get_notifier

logger = logging.getLogger(__name__)


def collect_news_for_stocks() -> str:
    """为监控列表中的品种采集新闻"""
    all_stocks = []
    for status in ['watching', 'focused', 'holding']:
        all_stocks.extend(get_stocks_by_watch_status(status))

    if not all_stocks:
        return "无监控品种，跳过新闻采集"

    total_news = 0
    alerts = 0

    for i, stock in enumerate(all_stocks):
        if i > 0:
            time.sleep(1)  # 每只股票间隔1秒，避免yfinance API限流
        try:
            news_items = _fetch_yahoo_news(stock['symbol'], stock['market'])
            for item in news_items:
                saved = _save_news(stock['id'], item)
                if saved:
                    total_news += 1
                    if item.get('is_alert'):
                        alerts += 1
                        _notify_alert(stock, item)
        except Exception as e:
            logger.warning(f"采集 {stock['symbol']} 新闻失败: {e}")

    summary = f"采集 {len(all_stocks)} 只品种新闻，新增 {total_news} 条，{alerts} 条异动"
    logger.info(f"[新闻采集] {summary}")
    return summary


def _fetch_yahoo_news(symbol: str, market: str) -> list:
    """通过 Yahoo Finance 获取新闻"""
    try:
        import yfinance as yf
        if market == 'hk':
            yahoo_sym = f"{int(symbol):04d}.HK"
        elif market == 'cn':
            suffix = '.SS' if symbol.startswith('6') else '.SZ'
            yahoo_sym = f"{symbol}{suffix}"
        else:
            yahoo_sym = symbol

        ticker = yf.Ticker(yahoo_sym)
        news = ticker.news or []

        result = []
        for item in news[:5]:
            content = item.get('content', {}) if isinstance(item, dict) else {}
            title = content.get('title', '') or item.get('title', '')
            summary = content.get('summary', '') or item.get('summary', '')
            pub_date = content.get('pubDate', '') or item.get('providerPublishTime', '')

            # 异动检测：关键词匹配
            alert_keywords = [
                'earnings', 'acquisition', 'merger', 'buyback', 'fda',
                'sanction', 'split', 'dividend', 'lawsuit', 'recall',
                '财报', '并购', '回购', '拆股', '分红', '制裁'
            ]
            is_alert = any(kw in (title + summary).lower() for kw in alert_keywords)

            if title:
                result.append({
                    "title": title,
                    "summary": summary[:500] if summary else "",
                    "source": "yahoo",
                    "url": content.get('canonicalUrl', {}).get('url', '') or item.get('link', ''),
                    "is_alert": is_alert,
                    "published_at": str(pub_date) if pub_date else "",
                })
        return result
    except Exception as e:
        logger.debug(f"Yahoo news fetch failed for {symbol}: {e}")
        return []


def _save_news(stock_id: str, item: dict) -> bool:
    """保存新闻到数据库（去重）"""
    with get_db() as conn:
        # 简单去重：标题相同则跳过
        existing = conn.execute(
            "SELECT id FROM stock_news WHERE stock_id=? AND title=?",
            (stock_id, item['title'])
        ).fetchone()
        if existing:
            return False

        news_id = str(uuid.uuid4())
        now = datetime.now().isoformat()
        conn.execute(
            """INSERT INTO stock_news
               (id, stock_id, title, summary, source, url, is_alert, published_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (news_id, stock_id, item['title'], item.get('summary', ''),
             item.get('source', ''), item.get('url', ''),
             1 if item.get('is_alert') else 0,
             item.get('published_at', ''), now)
        )

        # 更新股票的 news_alert 标记
        if item.get('is_alert'):
            conn.execute(
                "UPDATE stocks SET news_alert=1 WHERE id=?", (stock_id,)
            )

    return True


def _notify_alert(stock: dict, item: dict):
    """发送异动新闻通知"""
    notifier = get_notifier()
    if notifier:
        notifier.send_card(
            f"新闻异动: {stock['symbol']}",
            {
                "品种": f"{stock['symbol']} {stock.get('symbol_name', '')}",
                "标题": item['title'],
                "摘要": item.get('summary', '')[:200],
            }
        )


def get_stock_news(stock_id: str, limit: int = 20) -> list:
    """获取某只股票的新闻"""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT * FROM stock_news
               WHERE stock_id = ?
               ORDER BY created_at DESC LIMIT ?""",
            (stock_id, limit)
        ).fetchall()
    return [dict(r) for r in rows]
