"""CSV 导出服务"""
import csv
import io
import json
from web import config


# ─── CSV 表头定义 ───

CSV_HEADERS = [
    # 基本信息
    'symbol', 'symbol_name', 'market', 'end_date', 'tags', 'analyzed_at',
    # 算法六维评分
    'algo_DL', 'algo_PT', 'algo_LK', 'algo_SF', 'algo_TY', 'algo_DN',
    # 算法综合
    'algo_position', 'algo_conclusion',
    # 算法关键指标
    'dl_kline_count', 'dl_range_pct',
    'pt_touch_count', 'pt_platform_type',
    'lk_quality_score', 'lk_width_pct', 'lk_tail_break',
    'sf_tail_drift_pct', 'sf_direction',
    'ty_squeeze_length', 'ty_avg_range_ratio',
    'dn_force_ratio', 'dn_volume_ratio', 'dn_direction',
    # 人工标注评分
    'label_DL', 'label_PT', 'label_LK', 'label_SF', 'label_TY', 'label_DN',
    # 人工标注备注
    'dl_note', 'pt_note', 'lk_note', 'sf_note', 'ty_note', 'dn_note',
    # 人工判断
    'verdict', 'reason',
]


def _safe(val, default=''):
    """安全取值，None 转空串"""
    return val if val is not None else default


def _extract_score_card_fields(score_card_json):
    """从 score_card_json 提取关键指标，返回 dict"""
    fields = {}
    if not score_card_json:
        return fields
    try:
        card = json.loads(score_card_json)
    except (json.JSONDecodeError, TypeError):
        return fields

    # DL
    dl = card.get('dl_result')
    if dl:
        fields['dl_kline_count'] = dl.get('kline_count', '')
        fields['dl_range_pct'] = dl.get('range_pct', '')

    # PT
    pt = card.get('pt_result')
    if pt:
        fields['pt_touch_count'] = pt.get('touch_count', '')
        fields['pt_platform_type'] = pt.get('platform_type', '')

    # LK
    lk = card.get('lk_result')
    if lk:
        fields['lk_quality_score'] = lk.get('quality_score', '')
        fields['lk_width_pct'] = lk.get('width_pct', '')
        fields['lk_tail_break'] = lk.get('tail_break', '')

    # SF
    sf = card.get('sf_result')
    if sf:
        fields['sf_tail_drift_pct'] = sf.get('tail_drift_pct', '')
        fields['sf_direction'] = sf.get('direction', '')

    # TY
    ty = card.get('ty_result')
    if ty:
        fields['ty_squeeze_length'] = ty.get('squeeze_length', '')
        fields['ty_avg_range_ratio'] = ty.get('avg_range_ratio', '')

    # DN
    dn = card.get('dn_result')
    if dn:
        fields['dn_force_ratio'] = dn.get('force_ratio', '')
        fields['dn_volume_ratio'] = dn.get('volume_ratio', '')
        fields['dn_direction'] = dn.get('direction', '')

    # 综合
    fields['algo_conclusion'] = '; '.join(card.get('conclusion_lines', []))
    fields['algo_position'] = card.get('position_size', '')

    return fields


def _build_full_query(extra_join='', extra_where='', params=()):
    """构建完整查询，返回包含所有字段的行"""
    query = f"""
        SELECT s.id, s.symbol, s.symbol_name, s.market, s.end_date, s.analyzed_at,
               s.dl_grade AS algo_dl, s.pt_grade AS algo_pt, s.lk_grade AS algo_lk,
               s.sf_grade AS algo_sf, s.ty_grade AS algo_ty, s.dn_grade AS algo_dn,
               s.conclusion, s.position_size, s.score_card_json,
               l.dl_grade AS label_dl, l.dl_note,
               l.pt_grade AS label_pt, l.pt_note,
               l.lk_grade AS label_lk, l.lk_note,
               l.sf_grade AS label_sf, l.sf_note,
               l.ty_grade AS label_ty, l.ty_note,
               l.dn_grade AS label_dn, l.dn_note,
               l.verdict, l.reason
        FROM stocks s
        {extra_join}
        LEFT JOIN labels l ON l.stock_id = s.id
        {extra_where}
        ORDER BY s.symbol
    """
    return query, params


def _get_stock_tags_map(conn, stock_ids):
    """批量获取 tags"""
    if not stock_ids:
        return {}
    placeholders = ','.join('?' * len(stock_ids))
    rows = conn.execute(f"""
        SELECT st.stock_id, t.name
        FROM stock_tags st JOIN tags t ON t.id = st.tag_id
        WHERE st.stock_id IN ({placeholders})
    """, stock_ids).fetchall()
    result = {}
    for r in rows:
        result.setdefault(r['stock_id'], []).append(r['name'])
    return result


def _rows_to_csv(conn, rows, writer):
    """将查询结果写入 csv writer"""
    stock_ids = [r['id'] for r in rows]
    tags_map = _get_stock_tags_map(conn, stock_ids)

    for row in rows:
        sc = _extract_score_card_fields(row['score_card_json'])
        tags_str = ', '.join(tags_map.get(row['id'], []))

        writer.writerow([
            _safe(row['symbol']),
            _safe(row['symbol_name']),
            _safe(row['market']),
            _safe(row['end_date']),
            tags_str,
            _safe(row['analyzed_at']),
            # 算法评分
            _safe(row['algo_dl']),
            _safe(row['algo_pt']),
            _safe(row['algo_lk']),
            _safe(row['algo_sf']),
            _safe(row['algo_ty']),
            _safe(row['algo_dn']),
            # 算法综合
            sc.get('algo_position', _safe(row['position_size'])),
            sc.get('algo_conclusion', _safe(row['conclusion'])),
            # 算法关键指标
            sc.get('dl_kline_count', ''),
            sc.get('dl_range_pct', ''),
            sc.get('pt_touch_count', ''),
            sc.get('pt_platform_type', ''),
            sc.get('lk_quality_score', ''),
            sc.get('lk_width_pct', ''),
            sc.get('lk_tail_break', ''),
            sc.get('sf_tail_drift_pct', ''),
            sc.get('sf_direction', ''),
            sc.get('ty_squeeze_length', ''),
            sc.get('ty_avg_range_ratio', ''),
            sc.get('dn_force_ratio', ''),
            sc.get('dn_volume_ratio', ''),
            sc.get('dn_direction', ''),
            # 人工标注
            _safe(row['label_dl']),
            _safe(row['label_pt']),
            _safe(row['label_lk']),
            _safe(row['label_sf']),
            _safe(row['label_ty']),
            _safe(row['label_dn']),
            _safe(row['dl_note']),
            _safe(row['pt_note']),
            _safe(row['lk_note']),
            _safe(row['sf_note']),
            _safe(row['ty_note']),
            _safe(row['dn_note']),
            _safe(row['verdict']),
            _safe(row['reason']),
        ])


def sync_labels_to_csv(conn):
    """将所有已标注数据同步写入 labeled_cases.csv，供分析模块自回归测试使用。
    包含完整的算法结果、关键指标、人工标注对比数据。
    """
    query, params = _build_full_query(
        extra_where='WHERE l.id IS NOT NULL'
    )
    rows = conn.execute(query, params).fetchall()

    with open(str(config.LABELED_CASES_CSV), 'w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(CSV_HEADERS)
        _rows_to_csv(conn, rows, writer)


def export_csv(conn, tag_id: str = None) -> str:
    """导出数据为 CSV 字符串（包含完整的算法+标注信息）。
    tag_id 为 None 时导出全部数据，否则只导出该 tag 下的。
    """
    if tag_id:
        query, params = _build_full_query(
            extra_join='JOIN stock_tags st ON st.stock_id = s.id',
            extra_where='WHERE st.tag_id = ?',
            params=(tag_id,)
        )
    else:
        query, params = _build_full_query()

    rows = conn.execute(query, params).fetchall()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(CSV_HEADERS)
    _rows_to_csv(conn, rows, writer)
    return output.getvalue()
