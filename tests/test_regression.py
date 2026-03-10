"""
回归测试：对比分析算法结果 vs 人工标注

使用 data/labeled_cases.csv 中的标注数据作为基准，
重新运行分析并对比每个维度的评分。

用法:
    python tests/test_regression.py
    python tests/test_regression.py 600573       # 只测试单只
    python tests/test_regression.py --verbose     # 详细输出
"""
import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Windows 终端编码
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from core import (
    fetch_kline, detect_market, run_full_analysis,
    AnalyzerConfig, scorecard_to_dict, extract_grades
)


CSV_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        'data', 'labeled_cases.csv')

# 维度映射：CSV列名 → (algo列, label列)
DIMENSIONS = [
    ('DL', 'algo_DL', 'label_DL'),
    ('PT', 'algo_PT', 'label_PT'),
    ('LK', 'algo_LK', 'label_LK'),
    ('SF', 'algo_SF', 'label_SF'),
    ('TY', 'algo_TY', 'label_TY'),
    ('DN', 'algo_DN', 'label_DN'),
]


def load_labeled_cases(csv_path=CSV_PATH):
    """加载标注数据"""
    cases = []
    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row['symbol'].strip():
                cases.append(row)
    return cases


def run_analysis(symbol, end_date=None):
    """运行六维分析，返回 grades dict"""
    market = detect_market(symbol)
    df = fetch_kline(symbol=symbol, end_date=end_date or None, bars=300)
    if df is None or df.empty:
        raise ValueError(f"无法获取 {symbol} 数据")

    config = AnalyzerConfig()
    card = run_full_analysis(df, symbol=symbol, config=config, market=market)
    card_dict = scorecard_to_dict(card)
    grades = extract_grades(card_dict)

    # 提取关键指标
    details = {}
    sf = card_dict.get('sf_result', {})
    ty = card_dict.get('ty_result', {})
    details['sf_drift'] = sf.get('tail_drift_pct', 0)
    details['sf_reasoning'] = sf.get('reasoning', [])
    details['ty_squeeze'] = ty.get('squeeze_length', 0)
    details['ty_slope'] = ty.get('slope_pct', 0)
    details['ty_avg_ratio'] = ty.get('avg_range_ratio', 0)
    details['ty_reasoning'] = ty.get('reasoning', [])
    details['position'] = card_dict.get('position_size', '')
    details['conclusion'] = card_dict.get('conclusion_lines', [])

    return grades, details


def compare_grade(algo, label):
    """对比单个维度，返回 (match, old_algo, label)"""
    if not label:  # 人工未标注此维度
        return None, algo, label
    return algo == label, algo, label


def main():
    verbose = '--verbose' in sys.argv or '-v' in sys.argv
    target = None
    for arg in sys.argv[1:]:
        if not arg.startswith('-'):
            target = arg

    cases = load_labeled_cases()
    if not cases:
        print("错误：未找到标注数据")
        return

    if target:
        cases = [c for c in cases if c['symbol'] == target]
        if not cases:
            print(f"未找到 {target} 的标注数据")
            return

    print(f"回归测试：{len(cases)} 只股票")
    print("=" * 80)

    total_dims = 0
    match_count = 0
    improved = []
    regressed = []
    results_table = []

    for case in cases:
        symbol = case['symbol']
        end_date = case.get('end_date', '') or None
        symbol_name = case.get('symbol_name', '')
        print(f"\n{'─' * 60}")
        print(f"分析 {symbol} {symbol_name} (end_date={end_date or '今天'})")

        try:
            grades, details = run_analysis(symbol, end_date)
        except Exception as e:
            print(f"  ❌ 分析失败: {e}")
            continue

        row_result = {'symbol': symbol, 'name': symbol_name}
        dim_results = []

        for dim_name, algo_col, label_col in DIMENSIONS:
            old_algo = case.get(algo_col, '')  # CSV 中旧算法结果
            label = case.get(label_col, '')     # 人工标注
            new_algo = grades.get(f'{dim_name.lower()}_grade', '')

            if not label:
                dim_results.append((dim_name, '—', old_algo, new_algo, label, 'skip'))
                continue

            total_dims += 1
            old_match = (old_algo == label)
            new_match = (new_algo == label)

            if new_match:
                match_count += 1
                status = '✅'
            else:
                status = '❌'

            # 变化检测
            change = ''
            if old_algo != new_algo:
                change = f' ({old_algo}→{new_algo})'
                if not old_match and new_match:
                    improved.append(f"{symbol} {dim_name}: {old_algo}→{new_algo} (标注={label})")
                elif old_match and not new_match:
                    regressed.append(f"{symbol} {dim_name}: {old_algo}→{new_algo} (标注={label})")

            dim_results.append((dim_name, status, old_algo, new_algo, label, change))

        # 打印结果
        print(f"  {'维度':>4}  {'状态':>4}  {'旧算法':>6}  {'新算法':>6}  {'标注':>6}  变化")
        for dim_name, status, old_a, new_a, lbl, change in dim_results:
            if change == 'skip':
                print(f"  {dim_name:>4}  {'—':>4}  {old_a:>6}  {new_a:>6}  {'':>6}  未标注")
            else:
                print(f"  {dim_name:>4}  {status:>4}  {old_a:>6}  {new_a:>6}  {lbl:>6}  {change}")

        if verbose:
            print(f"\n  SF详情: drift={details['sf_drift']:.3f}%")
            for r in details['sf_reasoning']:
                print(f"    {r}")
            print(f"  TY详情: squeeze={details['ty_squeeze']}, slope={details['ty_slope']:.4f}%, ratio={details['ty_avg_ratio']:.3f}")
            for r in details['ty_reasoning']:
                print(f"    {r}")
            print(f"  仓位: {details['position']}")

    # 汇总
    print(f"\n{'=' * 80}")
    print(f"回归测试汇总")
    print(f"{'─' * 40}")
    accuracy = match_count / total_dims * 100 if total_dims > 0 else 0
    print(f"总维度: {total_dims}  匹配: {match_count}  准确率: {accuracy:.1f}%")

    if improved:
        print(f"\n✅ 改善 ({len(improved)}):")
        for item in improved:
            print(f"  + {item}")

    if regressed:
        print(f"\n❌ 退化 ({len(regressed)}):")
        for item in regressed:
            print(f"  - {item}")

    if not regressed:
        print(f"\n🎉 无退化！")

    return len(regressed) == 0


if __name__ == '__main__':
    success = main()
    sys.exit(0 if success else 1)
