"""基本面数据服务"""
import json
import logging
import time
from datetime import datetime
from typing import Optional

from web.database import get_db

logger = logging.getLogger(__name__)


def fetch_fundamentals(symbol: str, market: str) -> Optional[dict]:
    """获取基本面数据"""
    try:
        import yfinance as yf
        time.sleep(1)  # yfinance API限流保护
        if market == 'hk':
            yahoo_sym = f"{int(symbol):04d}.HK"
        elif market == 'cn':
            suffix = '.SS' if symbol.startswith('6') else '.SZ'
            yahoo_sym = f"{symbol}{suffix}"
        else:
            yahoo_sym = symbol

        ticker = yf.Ticker(yahoo_sym)
        info = ticker.info or {}

        fundamentals = {
            "name": info.get('longName') or info.get('shortName', ''),
            "sector": info.get('sector', ''),
            "industry": info.get('industry', ''),
            "market_cap": info.get('marketCap', 0),
            "pe_ratio": info.get('trailingPE') or info.get('forwardPE'),
            "pb_ratio": info.get('priceToBook'),
            "roe": info.get('returnOnEquity'),
            "revenue": info.get('totalRevenue'),
            "profit_margin": info.get('profitMargins'),
            "dividend_yield": info.get('dividendYield'),
            "beta": info.get('beta'),
            "52w_high": info.get('fiftyTwoWeekHigh'),
            "52w_low": info.get('fiftyTwoWeekLow'),
            "avg_volume": info.get('averageVolume'),
            "earnings_date": str(info.get('earningsTimestamp', '')),
            "updated_at": datetime.now().isoformat(),
        }
        return fundamentals
    except Exception as e:
        logger.warning(f"获取 {symbol} 基本面数据失败: {e}")
        return None


def refresh_fundamentals(stock_id: str) -> Optional[dict]:
    """刷新并缓存基本面数据"""
    with get_db() as conn:
        stock = conn.execute(
            "SELECT symbol, market FROM stocks WHERE id=?", (stock_id,)
        ).fetchone()
        if not stock:
            return None

    data = fetch_fundamentals(stock['symbol'], stock['market'])
    if data:
        with get_db() as conn:
            # 同时更新 symbol_name 如果为空
            conn.execute(
                """UPDATE stocks SET fundamental_json=?, updated_at=?,
                   symbol_name=CASE WHEN symbol_name='' THEN ? ELSE symbol_name END
                   WHERE id=?""",
                (json.dumps(data, ensure_ascii=False), datetime.now().isoformat(),
                 data.get('name', ''), stock_id)
            )
    return data


def get_cached_fundamentals(stock_id: str) -> Optional[dict]:
    """获取缓存的基本面数据"""
    with get_db() as conn:
        row = conn.execute(
            "SELECT fundamental_json FROM stocks WHERE id=?", (stock_id,)
        ).fetchone()
        if row and row['fundamental_json']:
            try:
                return json.loads(row['fundamental_json'])
            except (json.JSONDecodeError, TypeError):
                pass
    return None


def format_market_cap(value) -> str:
    """格式化市值显示"""
    if not value:
        return "-"
    v = float(value)
    if v >= 1e12:
        return f"{v/1e12:.1f}T"
    if v >= 1e9:
        return f"{v/1e9:.1f}B"
    if v >= 1e6:
        return f"{v/1e6:.0f}M"
    return str(int(v))
