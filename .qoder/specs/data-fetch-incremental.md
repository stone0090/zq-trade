# 数据获取增量优化

## Context
当前需要通过 `--csv` 手动指定本地数据文件，不够便捷。
目标：只需传入股票代码和截止日期，自动从本地缓存增量更新数据，获取最近500根小时K线。

---

## 1. CLI 入参改造 — main.py

移除 `--csv` 参数（保留向后兼容），调整 `--end` 为截止日期（默认今天），移除 `--start`。

```
python main.py analyze 600802                    # 默认到今天，取500根
python main.py analyze 600802 --end 2026-03-07   # 指定截止日期
python main.py analyze 600802 --csv xxx.csv      # 保留兼容，直接用文件
```

`cmd_analyze` 流程：
1. 如果有 `--csv`，直接加载文件（保持现有逻辑）
2. 否则，调用新的 `fetch_kline_smart(symbol, end_date, bars=500)` 获取数据

---

## 2. 市场识别 — fetcher.py

```python
def detect_market(symbol: str) -> str:
    """纯数字 → 'cn'，含字母 → 'us'"""
```

---

## 3. 缓存路径 — fetcher.py

按市场分目录：`data/cn/600802.csv`，`data/us/AAPL.csv`

```python
def _cache_path(symbol: str) -> Path:
    market = detect_market(symbol)
    return _get_data_dir() / market / f"{symbol}_hourly.csv"
```

---

## 4. 智能增量获取 — fetcher.py 核心函数

```python
def fetch_kline_smart(symbol: str, end_date: str = None, bars: int = 500) -> pd.DataFrame:
```

**流程：**
1. 计算目标时间范围：从 `end_date` 往前推算所需天数
   - A股：每天4根小时K线 → 500根 ≈ 125个交易日 ≈ 180天
   - 美股（预留）：每天7根 → 500根 ≈ 72个交易日 ≈ 100天
2. 读取本地缓存（如有）
3. 判断缺口：
   - 本地无数据 → 全量拉取
   - 本地最新时间 >= end_date → 直接用本地数据
   - 本地最新时间 < end_date → 增量拉取（从本地最新时间+1开始到end_date）
4. 合并：本地数据 + 增量数据 → 去重(按index) → 排序
5. 保存合并后的完整数据到缓存
6. 截取最近 bars 根返回

**增量拉取关键：**
```python
if local_df is not None:
    last_time = local_df.index[-1]
    if last_time >= end_dt:
        # 本地已覆盖，直接截取
        return local_df[local_df.index <= end_dt].tail(bars)
    # 增量：从 last_time 之后开始拉
    new_df = _fetch_from_akshare(symbol, start=last_time, end=end_dt)
    merged = pd.concat([local_df, new_df])
    merged = merged[~merged.index.duplicated(keep='last')].sort_index()
else:
    merged = _fetch_from_akshare(symbol, start=start_dt, end=end_dt)
```

---

## 5. 每日K线根数常量

```python
_BARS_PER_DAY = {
    'cn': 4,    # A股：10:30, 11:30, 14:00, 15:00
    'us': 7,    # 美股盘中：9:30~16:00，每小时1根（预留）
}
```

用于从 bars 反推需要拉取多少天的数据。

---

## 6. fetch_hourly_kline 保留兼容

原有 `fetch_hourly_kline` 函数保留但标记为旧接口，内部改为调用 `fetch_kline_smart`。

---

## 关键文件
- `main.py` — CLI 入参调整
- `src/data/fetcher.py` — 核心增量逻辑

## 验证方法
1. `python main.py analyze 600802 --end 2026-03-07` — 首次全量拉取，缓存到 data/cn/600802.csv
2. `python main.py analyze 600802 --end 2026-03-07` — 第二次应命中缓存，无需网络
3. 检查 data/cn/ 目录下生成了缓存文件
4. `python main.py analyze 600802 --csv data/600802_hourly.csv` — 旧方式仍可用
