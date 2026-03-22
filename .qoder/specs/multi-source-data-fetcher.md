# 多数据源轮换 - 实施计划

## 背景

Yahoo Finance API 频繁返回 403/Rate Limited 错误，导致港股/美股数据获取失败。当前架构每个市场仅有 2 个数据源（A股: AKShare + Sina；港美股: Yahoo v8 + yfinance），且港美股两个源底层都是 Yahoo API，实质上是单一数据源。需要增加更多数据源并实现智能轮换，当某个源被限流时自动降级到备用源。

## 改动文件

| 文件 | 改动内容 |
|------|---------|
| `core/data/fetcher.py` | 添加健康度追踪、调度器、4个新数据源获取函数，改造 `_fetch_cn` 和 `_fetch_hk_us` |
| `requirements.txt` | 添加 `efinance`、`baostock` 可选依赖 |

## 数据源矩阵（改造后）

| 市场 | 数据源链（优先级顺序） |
|------|----------------------|
| A股 | akshare -> efinance -> sina -> baostock |
| 港股/美股 | yahoo_v8 -> yfinance -> efinance -> twelvedata* |

\* twelvedata 仅在 settings 表中配置了 API Key 时启用

## 实施步骤

### Step 1: 健康度追踪基础设施

在 `fetcher.py` 的 `_throttle()` 函数后（约 line 41），插入以下模块级代码：

```python
# ─── 数据源健康度追踪 ───
_SOURCE_HEALTH = {}  # {source: {'fails': int, 'cooldown_until': float}}
_COOLDOWN_SCHEDULE = [0, 30, 120, 600, 1800]  # 冷却秒数，按连续失败次数递增


def _mark_success(source: str):
    """标记数据源请求成功，重置失败计数"""
    h = _SOURCE_HEALTH.setdefault(source, {'fails': 0, 'cooldown_until': 0.0})
    h['fails'] = 0
    h['cooldown_until'] = 0.0


def _mark_failure(source: str):
    """标记数据源请求失败，设置指数退避冷却期"""
    h = _SOURCE_HEALTH.setdefault(source, {'fails': 0, 'cooldown_until': 0.0})
    h['fails'] += 1
    idx = min(h['fails'], len(_COOLDOWN_SCHEDULE) - 1)
    h['cooldown_until'] = time.monotonic() + _COOLDOWN_SCHEDULE[idx]


def _is_available(source: str) -> bool:
    """检查数据源是否可用（不在冷却期）"""
    h = _SOURCE_HEALTH.get(source)
    if not h:
        return True
    return time.monotonic() >= h['cooldown_until']


# ─── 可选依赖库检测 ───
_LIB_AVAILABLE = {}


def _check_lib(name: str) -> bool:
    """检查可选依赖库是否已安装（结果缓存）"""
    if name in _LIB_AVAILABLE:
        return _LIB_AVAILABLE[name]
    try:
        if name == 'efinance':
            import efinance  # noqa: F401
        elif name == 'baostock':
            import baostock  # noqa: F401
        elif name == 'twelvedata':
            _LIB_AVAILABLE[name] = bool(_get_twelvedata_key())
            return _LIB_AVAILABLE[name]
        else:
            return True  # 内置源始终可用
        _LIB_AVAILABLE[name] = True
    except ImportError:
        _LIB_AVAILABLE[name] = False
    return _LIB_AVAILABLE[name]
```

冷却策略：连续失败 1 次冷却 30s，2 次冷却 2min，3 次冷却 10min，4+ 次冷却 30min。冷却期内源被跳过（除非是最后一个源）。

### Step 2: 统一调度器 `_try_sources`

紧接 Step 1 代码之后插入：

```python
def _try_sources(sources: list) -> pd.DataFrame:
    """
    按优先级逐个尝试数据源列表。
    sources: [(name, fetch_fn), ...]
    - 冷却期内的源被跳过（除非是最后一个）
    - 每个 fetch_fn 内部自行调用 _throttle()
    - 成功时 _mark_success()，失败时 _mark_failure()
    - 全部失败则抛异常
    """
    errors = []
    for i, (name, fetch_fn) in enumerate(sources):
        is_last = (i == len(sources) - 1)
        if not _is_available(name) and not is_last:
            h = _SOURCE_HEALTH.get(name, {})
            remaining = h.get('cooldown_until', 0) - time.monotonic()
            logger.info(f"  跳过 {name} (冷却中, 剩余 {remaining:.0f}s)")
            continue
        try:
            df = fetch_fn()
            _mark_success(name)
            return df
        except Exception as e:
            _mark_failure(name)
            errors.append(f"{name}: {e}")
            logger.warning(f"  {name} 获取失败: {e}")
    raise Exception(f"所有数据源均失败: {'; '.join(errors)}")
```

**关键设计**: 不在 `_try_sources` 中调用 `_throttle()`，因为每个 `_fetch_via_*` 函数已经内部处理节流。这保持了与现有代码的一致性。

### Step 3: 新增 A 股数据源函数

在 `_fetch_via_sina()` 之后、`_standardize_columns()` 之前插入：

#### 3a. efinance（东财另一端点）

```python
def _fetch_via_efinance(symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
    """通过 efinance (东财另一端点) 获取A股小时K线"""
    import efinance as ef

    _throttle('efinance')
    beg = start_date[:10].replace('-', '')  # YYYYMMDD
    end = end_date[:10].replace('-', '')
    df = ef.stock.get_quote_history(symbol, beg=beg, end=end, klt=60, fqt=1)

    if df is None or df.empty:
        raise ValueError(f"efinance 未返回 {symbol} 的数据")

    df = _standardize_columns(df)
    logger.info(f"  efinance 获取 {symbol} {len(df)} 根")
    return df
```

- 与 akshare 同为东财数据但 API 端点不同，限流策略独立
- `klt=60` 小时线，`fqt=1` 前复权
- 返回中文列名（开盘/收盘/最高/最低/成交量），复用 `_standardize_columns()`

#### 3b. baostock（免费，无需注册）

```python
def _fetch_via_baostock(symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
    """通过 baostock 获取A股60分钟K线（数据延迟约1天）"""
    import baostock as bs

    _throttle('baostock')
    prefix = 'sh' if symbol.startswith('6') else 'sz'
    code = f"{prefix}.{symbol}"

    lg = bs.login()
    try:
        rs = bs.query_history_k_data_plus(
            code,
            "date,time,open,high,low,close,volume",
            start_date=start_date[:10],
            end_date=end_date[:10],
            frequency="60",
            adjustflag="2"  # 前复权
        )
        rows = []
        while (rs.error_code == '0') and rs.next():
            rows.append(rs.get_row_data())
    finally:
        bs.logout()

    if not rows:
        raise ValueError(f"baostock 未返回 {symbol} 的数据")

    df = pd.DataFrame(rows, columns=rs.fields)
    # baostock time 格式: "YYYYMMDDHHmmssSSS" (17位)
    df['datetime'] = pd.to_datetime(df['time'].str[:12], format='%Y%m%d%H%M')
    df.set_index('datetime', inplace=True)
    df.rename(columns={
        'open': 'Open', 'high': 'High', 'low': 'Low',
        'close': 'Close', 'volume': 'Volume'
    }, inplace=True)
    df = df[['Open', 'High', 'Low', 'Close', 'Volume']]
    for col in df.columns:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    df.sort_index(inplace=True)

    logger.info(f"  baostock 获取 {symbol} {len(df)} 根")
    return df
```

- 免费，无需注册 API Key
- 数据延迟约 1 天，作为最后的兜底源
- 必须 `login()/logout()` 配对使用

### Step 4: 新增港股/美股数据源函数

在 `_fetch_yahoo_via_yfinance()` 之后、`_fetch_hk_us()` 之前插入：

#### 4a. efinance 国际版（港股/美股）

```python
def _fetch_via_efinance_intl(symbol: str, market: str) -> pd.DataFrame:
    """通过 efinance 获取港股/美股小时K线"""
    import efinance as ef

    _throttle('efinance')
    df = ef.stock.get_quote_history(symbol, klt=60, fqt=1)

    if df is None or df.empty:
        raise ValueError(f"efinance 未返回 {symbol} 的数据")

    df = _standardize_columns(df)
    logger.info(f"  efinance(intl) 获取 {symbol} {len(df)} 根")
    return df
```

- efinance 支持美股代码（如 'AAPL'），港股支持需测试
- 提供完全独立于 Yahoo 的数据源

#### 4b. Twelve Data（需要 API Key）

```python
def _get_twelvedata_key() -> str:
    """从 SQLite settings 表读取 Twelve Data API Key"""
    try:
        import sqlite3
        db_path = _get_data_dir() / 'zqtrade.db'
        if not db_path.exists():
            return None
        conn = sqlite3.connect(str(db_path))
        row = conn.execute(
            "SELECT value FROM settings WHERE key='twelvedata_api_key'"
        ).fetchone()
        conn.close()
        return row[0] if row and row[0] else None
    except Exception:
        return None


def _fetch_via_twelvedata(symbol: str, market: str, period: str = '6mo') -> pd.DataFrame:
    """通过 Twelve Data API 获取港股/美股小时K线（需要 API Key）"""
    import requests

    api_key = _get_twelvedata_key()
    if not api_key:
        raise ValueError("Twelve Data API Key 未配置")

    _throttle('twelvedata')

    # Symbol 转换
    if market == 'hk':
        td_symbol = f"{int(symbol):04d}:HKEX"
    else:
        td_symbol = symbol

    # period -> outputsize
    period_to_size = {'5d': 35, '1mo': 150, '3mo': 450, '6mo': 900}
    outputsize = period_to_size.get(period, 500)

    url = 'https://api.twelvedata.com/time_series'
    params = {
        'symbol': td_symbol,
        'interval': '1h',
        'outputsize': outputsize,
        'apikey': api_key,
    }

    resp = requests.get(url, params=params, timeout=20)
    resp.raise_for_status()
    data = resp.json()

    if data.get('status') == 'error':
        raise ValueError(f"Twelve Data: {data.get('message', 'unknown error')}")

    values = data.get('values', [])
    if not values:
        raise ValueError(f"Twelve Data 未返回 {symbol} 的数据")

    df = pd.DataFrame(values)
    df['datetime'] = pd.to_datetime(df['datetime'])
    df.set_index('datetime', inplace=True)
    df.rename(columns={
        'open': 'Open', 'high': 'High', 'low': 'Low',
        'close': 'Close', 'volume': 'Volume'
    }, inplace=True)
    df = df[['Open', 'High', 'Low', 'Close', 'Volume']]
    for col in df.columns:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    df.sort_index(inplace=True)

    logger.info(f"  Twelve Data 获取 {symbol} {len(df)} 根")
    return df
```

- 免费层：800 次/天，8 次/分钟
- API Key 从 SQLite settings 表读取（key = `twelvedata_api_key`）
- 未配置 Key 时此源自动跳过（`_check_lib('twelvedata')` 返回 False）

### Step 5: 改造 `_fetch_cn()` (line 256-283)

替换现有手动 try/except 链为调度器模式：

```python
def _fetch_cn(symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
    """拉取A股小时K线 — 多源轮换"""
    # 预计算 Sina 需要的 datalen
    start_dt = pd.to_datetime(start_date)
    end_dt = pd.to_datetime(end_date)
    days_span = (end_dt - start_dt).days
    sina_datalen = max(1500, days_span * 4)

    def _sina_with_filter():
        df = _fetch_via_sina(symbol, datalen=sina_datalen)
        if df is not None and not df.empty:
            df = df[(df.index >= start_dt) & (df.index <= end_dt)]
            if not df.empty:
                return df
        raise ValueError("Sina Finance 未返回有效数据")

    sources = [
        ('akshare', lambda: _fetch_via_akshare(symbol, start_date, end_date)),
    ]
    if _check_lib('efinance'):
        sources.append(('efinance', lambda: _fetch_via_efinance(symbol, start_date, end_date)))
    sources.append(('sina', _sina_with_filter))
    if _check_lib('baostock'):
        sources.append(('baostock', lambda: _fetch_via_baostock(symbol, start_date, end_date)))

    return _try_sources(sources)
```

### Step 6: 改造 `_fetch_hk_us()` (line 476-490)

添加 `symbol` 和 `market` 可选参数，使用调度器：

```python
def _fetch_hk_us(yahoo_symbol: str, period: str = '6mo',
                 symbol: str = None, market: str = None) -> pd.DataFrame:
    """港股/美股K线获取 — 多源轮换"""
    sources = [
        ('yahoo', lambda: _fetch_yahoo(yahoo_symbol, period=period)),
        ('yfinance', lambda: _fetch_yahoo_via_yfinance(yahoo_symbol, period=period)),
    ]

    orig_sym = symbol or yahoo_symbol.replace('.HK', '')
    mkt = market or 'us'

    if _check_lib('efinance'):
        sources.append(('efinance', lambda: _fetch_via_efinance_intl(orig_sym, mkt)))
    if _check_lib('twelvedata'):
        sources.append(('twelvedata', lambda: _fetch_via_twelvedata(orig_sym, mkt, period)))

    try:
        return _try_sources(sources)
    except Exception as e:
        logger.error(f"  港股/美股所有数据源均失败: {e}")
        return None
```

**关键**: 保持原有返回 `None` 的行为（调用方检查 `None`），用 try/except 包裹 `_try_sources` 的异常。

### Step 7: 更新 `fetch_kline_smart()` 中的调用点

在 `fetch_kline_smart()` 内两处调用 `_fetch_hk_us` 的地方传入 `symbol` 和 `market`：

**Line 152** (增量拉取):
```python
# 旧: new_df = _fetch_hk_us(yahoo_sym, period=period)
# 新:
new_df = _fetch_hk_us(yahoo_sym, period=period, symbol=symbol, market=market)
```

**Line 169** (全量拉取):
```python
# 旧: merged = _fetch_hk_us(yahoo_sym, period='6mo')
# 新:
merged = _fetch_hk_us(yahoo_sym, period='6mo', symbol=symbol, market=market)
```

### Step 8: 更新 `requirements.txt`

```
# 数据获取（可选扩展源）
efinance>=0.4.0       # A股/美股备用数据源（可选）
baostock>=0.8.0       # A股备用数据源（可选，免费无需注册）
```

## 不改动的部分

- `fetch_kline_smart()` 函数签名和缓存逻辑 — 不变
- CSV 缓存系统 — 不变
- `detect_market()` — 不变
- `get_stock_name()` — 不变（后续可扩展，本次不涉及）
- `_standardize_columns()` — 不变
- 设置页 UI — 不涉及（可通过现有 `PUT /api/settings` 接口设置 `twelvedata_api_key`）

## 验证方式

1. 安装新依赖: `pip install efinance baostock`
2. A 股测试: `python -c "from core.data.fetcher import fetch_kline_smart; df = fetch_kline_smart('600802'); print(len(df), 'bars')"`
3. 港股测试: `python -c "from core.data.fetcher import fetch_kline_smart; df = fetch_kline_smart('02610'); print(len(df), 'bars')"`
4. 美股测试: `python -c "from core.data.fetcher import fetch_kline_smart; df = fetch_kline_smart('HIMS'); print(len(df), 'bars')"`
5. 检查日志: 观察源轮换日志 "跳过 xxx (冷却中)"、"xxx 获取失败" 等
6. 回归测试: `python tests/test_regression.py`
7. Web UI 测试: 启动服务，进入详情页触发重新分析，验证数据加载正常
