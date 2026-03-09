"""
批量生成所有标注案例的K线分析图表
"""
import sys
import csv
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from src.data.fetcher import fetch_kline_smart, get_stock_name, detect_market
from src.analyzer.scorer import run_full_analysis
from src.analyzer.base import AnalyzerConfig
from src.report.charger import generate_chart


def main():
    cases_file = Path(__file__).parent / 'data' / 'labeled_cases.csv'
    with open(cases_file, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        cases = list(reader)

    output_dir = "charts"
    print(f"共 {len(cases)} 个案例，图表输出到 {output_dir}/\n")

    for i, case in enumerate(cases):
        symbol = case['symbol']
        end_date = case['end_date']
        print(f"[{i+1}/{len(cases)}] {symbol} @ {end_date} ... ", end='', flush=True)

        if i > 0:
            time.sleep(2)

        try:
            df = fetch_kline_smart(symbol=symbol, end_date=end_date, bars=300)
        except Exception as e:
            print(f"数据获取失败: {e}")
            continue

        if df is None or df.empty:
            print("数据为空，跳过")
            continue

        config = AnalyzerConfig()
        card = run_full_analysis(df, symbol=symbol, config=config)
        card.symbol_name = get_stock_name(symbol)
        card.market = detect_market(symbol)

        # 自定义文件名: symbol_enddate.png
        date_tag = end_date.replace('-', '')
        out_path = Path(output_dir)
        out_path.mkdir(parents=True, exist_ok=True)
        filename = f"{symbol}_{date_tag}.png"
        filepath = str(out_path / filename)

        # 直接调用内部构建函数并保存
        from src.report.charger import _build_chart
        import matplotlib.pyplot as plt

        fig = _build_chart(df, card)
        fig.savefig(filepath, dpi=150, bbox_inches='tight', facecolor='white')
        plt.close(fig)

        print(f"OK -> {filepath}")

    print(f"\n全部完成！图表在 {output_dir}/ 目录下")


if __name__ == '__main__':
    main()
