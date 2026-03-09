"""CSV 导出服务"""
import csv
import io
import json


def export_batch_csv(conn, batch_id: str) -> str:
    """导出批次标注数据为 CSV 字符串，兼容 labeled_cases.csv 格式"""
    rows = conn.execute("""
        SELECT s.symbol, s.end_date,
               l.dl_grade, l.dl_note,
               l.pt_grade, l.pt_note,
               l.lk_grade, l.lk_note,
               l.sf_grade,
               l.ty_grade, l.ty_note,
               l.dn_grade,
               l.verdict, l.reason,
               s.score_card_json
        FROM stocks s
        LEFT JOIN labels l ON l.stock_id = s.id
        WHERE s.batch_id = ?
        ORDER BY s.created_at
    """, (batch_id,)).fetchall()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        'symbol', 'end_date',
        'DL', 'DL_length', 'PT', 'PT_note',
        'LK', 'LK_note', 'SF',
        'TY', 'TY_note', 'DN',
        'verdict', 'reason'
    ])

    for row in rows:
        # 从 score_card_json 提取 DL_length
        dl_length = ''
        if row['score_card_json']:
            try:
                card = json.loads(row['score_card_json'])
                dl = card.get('dl_result')
                if dl:
                    dl_length = dl.get('kline_count', '')
            except (json.JSONDecodeError, TypeError):
                pass

        writer.writerow([
            row['symbol'],
            row['end_date'] or '',
            row['dl_grade'] or '',
            dl_length,
            row['pt_grade'] or '',
            row['pt_note'] or '',
            row['lk_grade'] or '',
            row['lk_note'] or '',
            row['sf_grade'] or '',
            row['ty_grade'] or '',
            row['ty_note'] or '',
            row['dn_grade'] or '',
            row['verdict'] or '',
            row['reason'] or '',
        ])

    return output.getvalue()
