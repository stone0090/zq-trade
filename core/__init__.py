"""
ZQ-Trade 核心分析引擎 - Public API

面向 Agent 的一站式接口，一行导入即可完成六维K线分析。

快速使用:
    from core import analyze
    card = analyze("600802")                    # A股
    card = analyze("HIMS", end_date="2026-03-07")  # 美股 + 指定日期

底层调用:
    from core import fetch_kline, run_full_analysis, ScoreCard, AnalyzerConfig
    df = fetch_kline("600802", bars=300)
    card = run_full_analysis(df, symbol="600802")
"""

# ─── 数据获取 ───
from core.data.fetcher import (
    fetch_kline_smart as fetch_kline,
    detect_market,
    get_stock_name,
)

# ─── 分析引擎 ───
from core.analyzer.scorer import run_full_analysis

# ─── 数据类型 ───
from core.types import (
    ScoreCard,
    AnalyzerConfig,
    GradeScore,
    ReleaseLevel,
    StructureResult,
    PlatformResult,
    ContourResult,
    SqueezeResult,
    MomentumResult,
    ReleaseResult,
)

# ─── 序列化 ───
from core.serializer import scorecard_to_dict, extract_grades


def analyze(symbol: str, end_date: str = None, bars: int = 300,
            config: AnalyzerConfig = None) -> ScoreCard:
    """
    一站式分析：获取数据 → 六维分析 → 返回 ScoreCard。

    Args:
        symbol: 股票代码（如 "600802", "02610", "HIMS"）
        end_date: 截止日期 YYYY-MM-DD，默认今天
        bars: 获取K线根数，默认 300
        config: 分析器配置，默认使用标准配置

    Returns:
        ScoreCard: 包含六维分析结果、结论、仓位建议的完整评分卡
    """
    market = detect_market(symbol)
    df = fetch_kline(symbol=symbol, end_date=end_date, bars=bars)
    if df is None or df.empty:
        raise ValueError(f"未能获取到 {symbol} 的有效数据")
    cfg = config or AnalyzerConfig()
    card = run_full_analysis(df, symbol=symbol, config=cfg, market=market)
    card.symbol_name = get_stock_name(symbol)
    card.market = market
    return card


__all__ = [
    # 一站式
    'analyze',
    # 数据获取
    'fetch_kline', 'detect_market', 'get_stock_name',
    # 分析引擎
    'run_full_analysis',
    # 数据类型
    'ScoreCard', 'AnalyzerConfig', 'GradeScore', 'ReleaseLevel',
    'StructureResult', 'PlatformResult', 'ContourResult',
    'SqueezeResult', 'MomentumResult', 'ReleaseResult',
    # 序列化
    'scorecard_to_dict', 'extract_grades',
]
