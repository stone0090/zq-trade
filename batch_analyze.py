"""
批量分析指定股票并生成图表到 charts/test/
支持 A股/港股/美股
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from src.data.fetcher import fetch_kline_smart, get_stock_name, detect_market
from src.analyzer.scorer import run_full_analysis
from src.analyzer.base import AnalyzerConfig
from src.report.charger import _build_chart
import matplotlib.pyplot as plt


# ─── 待分析股票列表 ───

STOCKS = [
    # A股
    '603817', '600802', '600573', '600377', '000524',
    # 港股
    '02610', '01735',
    # 美股
    'PHR', 'OMER', 'JOBY', 'IMRX', 'HIMS', 'FOX', 'BLNK', 'BKKT', 'BAH',
]

OUTPUT_DIR = Path("charts/test")


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    total = len(STOCKS)
    print(f"共 {total} 只股票，图表输出到 {OUTPUT_DIR}/\n")

    ok_count = 0
    fail_count = 0

    for i, symbol in enumerate(STOCKS):
        market = detect_market(symbol)
        market_label = {'cn': 'A股', 'hk': '港股', 'us': '美股'}[market]
        print(f"[{i+1}/{total}] {symbol} ({market_label}) ... ", flush=True)

        if i > 0:
            time.sleep(2)

        # 获取数据
        try:
            df = fetch_kline_smart(symbol=symbol, end_date=None, bars=300)
        except Exception as e:
            print(f"  数据获取失败: {e}\n")
            fail_count += 1
            continue

        if df is None or df.empty:
            print("  数据为空，跳过\n")
            fail_count += 1
            continue

        # 分析
        config = AnalyzerConfig()
        card = run_full_analysis(df, symbol=symbol, config=config)
        card.symbol_name = get_stock_name(symbol)
        card.market = market

        # 生成图表
        filename = f"{symbol}.png"
        filepath = str(OUTPUT_DIR / filename)

        fig = _build_chart(df, card)
        fig.savefig(filepath, dpi=150, bbox_inches='tight', facecolor='white')
        plt.close(fig)

        print(f"  OK -> {filepath}\n")
        ok_count += 1

    print(f"\n完成! 成功 {ok_count}/{total}，失败 {fail_count}/{total}")
    print(f"图表在 {OUTPUT_DIR}/ 目录下")


if __name__ == '__main__':
    main()
