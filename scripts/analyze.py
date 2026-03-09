"""
六维打分开仓分析工具 - CLI入口

使用方式:
    python main.py analyze <symbol> [options]

示例:
    python main.py analyze 600802                    # 默认到今天，取400根
    python main.py analyze 600802 --end 2026-03-07   # 指定截止日期
    python main.py analyze 600802 --chart            # 生成K线分析图表
"""
import sys
import argparse
from pathlib import Path

# 将项目根目录加入 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main():
    parser = argparse.ArgumentParser(
        description='六维打分开仓分析工具 - 小时K线交易条件分析'
    )
    subparsers = parser.add_subparsers(dest='command', help='可用命令')

    # analyze 命令
    p_analyze = subparsers.add_parser('analyze', help='分析单只股票的开仓条件')
    p_analyze.add_argument('symbol', help='股票代码，如 600802')
    p_analyze.add_argument('--end', help='截止日期 YYYY-MM-DD（默认今天）')
    p_analyze.add_argument('--bars', type=int, default=400,
                           help='获取K线根数（默认400）')
    p_analyze.add_argument('--no-cache', action='store_true', help='禁用数据缓存')
    p_analyze.add_argument('--chart', action='store_true', help='生成K线分析图表（PNG）')

    args = parser.parse_args()

    if args.command == 'analyze':
        cmd_analyze(args)
    else:
        parser.print_help()


def cmd_analyze(args):
    """执行六维打分分析"""
    from core import analyze
    from core.report.printer import print_score_card

    # 1. 一站式分析
    try:
        card = analyze(args.symbol, end_date=args.end, bars=args.bars)
    except Exception as e:
        print(f"分析失败: {e}")
        sys.exit(1)

    # 2. 输出报告
    print_score_card(card)

    # 3. 生成图表（可选）
    if args.chart:
        from core.report.chart import generate_chart
        from core import fetch_kline
        df = fetch_kline(symbol=args.symbol, end_date=args.end, bars=args.bars)
        path = generate_chart(df, card)
        print(f"\n图表已保存: {path}")


if __name__ == '__main__':
    main()
