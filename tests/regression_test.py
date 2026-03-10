"""回归测试: 对比算法输出与人工标注"""
import csv
import sys
sys.stdout.reconfigure(encoding='utf-8')

from core import analyze

with open('data/labeled_cases.csv', encoding='utf-8') as f:
    reader = csv.DictReader(f)
    cases = [r for r in reader if r['symbol'].strip()]

dims = ['DL', 'PT', 'LK', 'SF', 'TY', 'DN']
total = 0
match = 0
regressions = []
improvements = []

for c in cases:
    sym = c['symbol']
    name = c['symbol_name']
    end = c['end_date'] if c['end_date'] else None
    try:
        card = analyze(sym, end_date=end)
    except Exception as e:
        print('ERROR %s: %s' % (sym, e))
        continue

    algo = {}
    algo['DL'] = str(card.dl_result.score) if card.dl_result else '?'
    if card.pt_result and card.pt_result.resistance_price > 0:
        algo['PT'] = str(card.pt_result.resistance_score)
    else:
        algo['PT'] = '?'
    algo['LK'] = str(card.lk_result.score) if card.lk_result else '?'
    algo['SF'] = str(card.sf_result.score) if card.sf_result else '?'
    if card.ty_result and card.ty_result.pending:
        algo['TY'] = '待定'
    else:
        algo['TY'] = str(card.ty_result.score) if card.ty_result else '?'
    if card.dn_result and card.dn_result.pending:
        algo['DN'] = '待定'
    else:
        algo['DN'] = str(card.dn_result.score) if card.dn_result else '?'

    label = {}
    for d in dims:
        label[d] = c.get('label_' + d, '')

    sym_match = 0
    sym_total = 0
    for d in dims:
        if not label[d]:
            continue
        sym_total += 1
        total += 1
        if algo[d] == label[d]:
            match += 1
            sym_match += 1
        else:
            old_algo = c.get('algo_' + d, '')
            if old_algo == label[d]:
                regressions.append('%s %s: %s -> %s (label=%s)' % (sym, d, old_algo, algo[d], label[d]))
            elif algo[d] == label[d]:
                improvements.append('%s %s: %s -> %s' % (sym, d, old_algo, algo[d]))

    status = 'OK' if sym_match == sym_total else '%d/%d' % (sym_match, sym_total)
    mismatches = ['%s:%s!=%s' % (d, algo[d], label[d]) for d in dims if label[d] and algo[d] != label[d]]
    mm_str = ' | '.join(mismatches) if mismatches else ''
    print('%s %-8s  %-6s  %s' % (sym, name, status, mm_str))

print()
print('Total: %d/%d (%.1f%%)' % (match, total, match / total * 100))
print()
if improvements:
    print('Improvements:')
    for imp in improvements:
        print('  + ' + imp)
print()
if regressions:
    print('REGRESSIONS:')
    for reg in regressions:
        print('  - ' + reg)
else:
    print('No regressions!')
