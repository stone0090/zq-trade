"""
批量对比脚本：人工标注 vs 算法输出

读取 data/labeled_cases.csv，逐个运行六维分析，输出对比表。
"""
import sys
import csv
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import fetch_kline, detect_market, run_full_analysis, AnalyzerConfig, GradeScore, ReleaseLevel


def grade_str(score) -> str:
    if isinstance(score, GradeScore):
        return str(score).split('.')[-1]
    if isinstance(score, ReleaseLevel):
        mapping = {ReleaseLevel.FIRST: '1st', ReleaseLevel.SECOND: '2nd', ReleaseLevel.THIRD: '3rd'}
        return mapping.get(score, '?')
    return str(score)


def run_one(symbol: str, end_date: str):
    """运行单个案例，返回各维度结果字典"""
    try:
        df = fetch_kline(symbol=symbol, end_date=end_date, bars=300)
    except Exception as e:
        return {'error': str(e)}

    if df.empty:
        return {'error': '数据为空'}

    config = AnalyzerConfig()
    card = run_full_analysis(df, symbol=symbol, config=config)
    card.market = detect_market(symbol)

    result = {
        'DL': grade_str(card.dl_result.score) if card.dl_result else 'F',
        'PT': '?',
        'PT_detail': '',
        'LK': '?',
        'LK_detail': '',
        'SF': '?',
        'TY': '?',
        'TY_detail': '',
        'DN': '?',
        'DN_detail': '',
        'verdict': '',
        'klines': len(df),
    }

    if card.early_terminated:
        result['verdict'] = '不做(DLF)'
        return result

    # PT
    if card.pt_result:
        pt = card.pt_result
        result['PT'] = grade_str(pt.score)
        details = []
        if pt.resistance_price > 0:
            details.append(f"阻力{pt.resistance_zone_low:.2f}~{pt.resistance_zone_high:.2f}"
                           f"({grade_str(pt.resistance_score)},触{pt.resistance_touch_count}次"
                           f"/透{pt.resistance_penetrations}次)")
        if pt.support_price > 0:
            details.append(f"支撑{pt.support_zone_low:.2f}~{pt.support_zone_high:.2f}"
                           f"({grade_str(pt.support_score)},触{pt.support_touch_count}次"
                           f"/透{pt.support_penetrations}次)")
        result['PT_detail'] = ' | '.join(details)

    # LK
    if card.lk_result:
        lk = card.lk_result
        result['LK'] = grade_str(lk.score)
        result['LK_detail'] = (f"质量{lk.quality_score:.2f} CV{lk.range_cv:.3f} "
                                f"异常{lk.abnormal_count}根({lk.abnormal_ratio*100:.1f}%)")

    # SF
    if card.sf_result:
        result['SF'] = grade_str(card.sf_result.score)
        sf = card.sf_result
        result['SF_detail'] = (f"尾部偏移{sf.tail_drift_pct:.2f}% "
                                f"尾长{sf.tail_length}根")
        if sf.reasoning:
            result['SF_detail'] += f" [{sf.reasoning[0]}]"

    # TY
    if card.ty_result:
        ty = card.ty_result
        result['TY'] = grade_str(ty.score)
        result['TY_detail'] = (f"长{ty.squeeze_length}根 range/ATR={ty.avg_range_ratio*100:.1f}% "
                                f"斜率{ty.slope_pct:.4f}%")

    # DN
    if card.dn_result:
        dn = card.dn_result
        if dn.pending:
            result['DN'] = 'C'
            result['DN_detail'] = '待定'
        else:
            result['DN'] = grade_str(dn.score)
            result['DN_detail'] = (f"方向={dn.direction} 力度={dn.force_ratio:.1f}x "
                                    f"破平台={dn.broke_platform}")

    # verdict
    if card.conclusion_lines:
        result['verdict'] = card.conclusion_lines[0]
    else:
        result['verdict'] = card.action_recommendation

    return result


def main():
    cases_file = Path(__file__).parent / 'data' / 'labeled_cases.csv'
    with open(cases_file, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        cases = list(reader)

    print(f"共 {len(cases)} 个案例\n")
    print("=" * 120)

    results = []
    for i, case in enumerate(cases):
        symbol = case['symbol']
        end_date = case['end_date']
        print(f"\n[{i+1}/{len(cases)}] {symbol} @ {end_date}")
        print("-" * 80)

        # Rate limit: pause between fetches
        if i > 0:
            import time
            time.sleep(2)

        algo = run_one(symbol, end_date)

        if 'error' in algo:
            print(f"  ERROR: {algo['error']}")
            results.append({'symbol': symbol, 'end_date': end_date, 'error': algo['error']})
            continue

        # 对比输出
        dims = ['DL', 'PT', 'LK', 'SF', 'TY', 'DN']
        print(f"  {'维度':<6} {'人工':<6} {'算法':<6} {'匹配':<6} 算法详情")
        print(f"  {'-'*70}")
        mismatches = []
        for dim in dims:
            human = case.get(dim, '?')
            algo_val = algo.get(dim, '?')
            match = 'OK' if human == algo_val or human == '?' else 'XX'
            detail = algo.get(f'{dim}_detail', '')
            print(f"  {dim:<6} {human:<6} {algo_val:<6} {match:<6} {detail}")
            if match == 'XX':
                mismatches.append(f"{dim}: 人工{human} vs 算法{algo_val}")

        print(f"\n  人工判断: {case.get('verdict', '')} | {case.get('reason', '')}")
        print(f"  算法结论: {algo.get('verdict', '')}")
        if mismatches:
            print(f"  >>> 偏差: {'; '.join(mismatches)}")
        else:
            print(f"  >>> 全部匹配")

        results.append({
            'symbol': symbol, 'end_date': end_date,
            'mismatches': mismatches, 'algo': algo, 'human': case
        })

    # 汇总
    print("\n" + "=" * 120)
    print("偏差汇总")
    print("=" * 120)

    dim_miss = {d: [] for d in ['DL', 'PT', 'LK', 'SF', 'TY', 'DN']}
    for r in results:
        if 'error' in r:
            continue
        for mm in r.get('mismatches', []):
            dim = mm.split(':')[0]
            if dim in dim_miss:
                dim_miss[dim].append(f"{r['symbol']}: {mm}")

    for dim, items in dim_miss.items():
        if items:
            print(f"\n{dim} 偏差 ({len(items)}个):")
            for item in items:
                print(f"  - {item}")
        else:
            print(f"\n{dim}: 全部匹配")

    total = len([r for r in results if 'error' not in r])
    total_mm = sum(1 for r in results if r.get('mismatches'))
    print(f"\n总计: {total}个案例, {total_mm}个存在偏差")


if __name__ == '__main__':
    main()
