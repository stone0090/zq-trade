"""股票管理 API"""
import json
import uuid
import threading
from pathlib import Path
from datetime import datetime
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from typing import Optional

from web.database import get_db
from web.models import StockListItem, StockDetail, StockImport, StockUpdate, BatchUpdate, ImportResult, AnalysisProgress
from web.config import DB_PATH, CHARTS_DIR
from web.services.analysis import analyze_stocks_sync, analyze_stock as analyze_single, get_progress, is_running
from web.services.export import sync_labels_to_csv

router = APIRouter(prefix="/api/stocks", tags=["stocks"])


@router.get("")
def list_stocks(
    tag: Optional[str] = Query(None),
    label_status: Optional[str] = Query(None),
    market: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    dl: Optional[str] = Query(None),
    pt: Optional[str] = Query(None),
    lk: Optional[str] = Query(None),
    sf: Optional[str] = Query(None),
    ty: Optional[str] = Query(None),
    dn: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=10000),
):
    with get_db() as conn:
        conditions = []
        params = []

        # 标注模块只显示有截止日期的记录（排除品种库记录）
        conditions.append("s.end_date IS NOT NULL")

        if tag:
            conditions.append(
                "s.id IN (SELECT st.stock_id FROM stock_tags st JOIN tags t ON t.id=st.tag_id WHERE t.name=?)"
            )
            params.append(tag)
        if market:
            conditions.append("s.market = ?")
            params.append(market)
        if status:
            conditions.append("s.status = ?")
            params.append(status)
        if search:
            search_term = f"%{search}%"
            conditions.append(
                "(s.symbol LIKE ? OR s.symbol_name LIKE ? OR s.end_date LIKE ? OR s.market LIKE ?)"
            )
            params.extend([search_term, search_term, search_term, search_term])

        # 各维度评级筛选（空字符串视为NULL，优先用人工标注）
        _grade_map = {'S': ('S',), 'A': ('S', 'A'), 'B': ('S', 'A', 'B')}
        _sf_map = {'1st': ('1st',), '2nd': ('1st', '2nd')}
        for col, val, mapping in [
            ('dl_grade', dl, _grade_map),
            ('pt_grade', pt, _grade_map),
            ('lk_grade', lk, _grade_map),
            ('ty_grade', ty, _grade_map),
            ('dn_grade', dn, _grade_map),
            ('sf_grade', sf, _sf_map),
        ]:
            if val and val in mapping:
                allowed = mapping[val]
                placeholders = ','.join('?' * len(allowed))
                conditions.append(f"COALESCE(NULLIF(l.{col}, ''), s.{col}) IN ({placeholders})")
                params.extend(allowed)

        where = ""
        if conditions:
            where = "WHERE " + " AND ".join(conditions)

        # label_status 需要在 HAVING 或者后过滤
        # 使用 NULLIF 将空字符串转为 NULL，确保 COALESCE 能正确回退到 stocks 表的值
        query = f"""
            SELECT s.id, s.symbol, s.symbol_name, s.market, s.end_date, s.status,
                   COALESCE(NULLIF(l.dl_grade, ''), s.dl_grade) as dl_grade,
                   COALESCE(NULLIF(l.pt_grade, ''), s.pt_grade) as pt_grade,
                   COALESCE(NULLIF(l.lk_grade, ''), s.lk_grade) as lk_grade,
                   COALESCE(NULLIF(l.sf_grade, ''), s.sf_grade) as sf_grade,
                   COALESCE(NULLIF(l.ty_grade, ''), s.ty_grade) as ty_grade,
                   COALESCE(NULLIF(l.dn_grade, ''), s.dn_grade) as dn_grade,
                   COALESCE(NULLIF(l.reason, ''), s.conclusion) as conclusion,
                   COALESCE(NULLIF(l.verdict, ''), s.position_size) as position_size,
                   s.analyzed_at,
                   s.updated_at,
                   s.kline_end_time,
                   CASE WHEN l.id IS NOT NULL THEN 'labeled' ELSE 'unlabeled' END as label_status
            FROM stocks s
            LEFT JOIN labels l ON l.stock_id = s.id
            {where}
            ORDER BY s.created_at ASC
        """
        rows = conn.execute(query, params).fetchall()

        # label_status 后过滤
        if label_status:
            rows = [r for r in rows if r['label_status'] == label_status]

        # 分页
        total = len(rows)
        total_pages = (total + page_size - 1) // page_size if page_size > 0 else 0
        offset = (page - 1) * page_size
        paged_rows = rows[offset:offset + page_size]

        # 获取每只股票的 tags
        stock_ids = [r['id'] for r in paged_rows]
        stock_tags_map = _get_stock_tags_map(conn, stock_ids)

    items = [_row_to_list_item(r, stock_tags_map.get(r['id'], [])) for r in paged_rows]
    return {
        "items": [item.model_dump() for item in items],
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
    }


@router.get("/progress", response_model=AnalysisProgress)
def get_analysis_progress():
    p = get_progress()
    return AnalysisProgress(**p)


@router.post("/import", response_model=ImportResult)
def import_stocks(req: StockImport):
    symbols = [s.strip() for s in req.symbols if s.strip()]
    if not symbols:
        raise HTTPException(400, "股票列表不能为空")
    if not req.end_date:
        raise HTTPException(400, "标注导入必须指定截止日期 end_date")

    now = datetime.now().isoformat()
    imported = 0
    skipped = 0
    new_stock_ids = []

    with get_db() as conn:
        # 确保 tags 存在
        tag_ids = []
        if req.tags:
            for tag_name in req.tags:
                tag_name = tag_name.strip()
                if not tag_name:
                    continue
                row = conn.execute("SELECT id FROM tags WHERE name=?", (tag_name,)).fetchone()
                if row:
                    tag_ids.append(row['id'])
                else:
                    tag_id = str(uuid.uuid4())
                    conn.execute(
                        "INSERT INTO tags (id, name, created_at) VALUES (?,?,?)",
                        (tag_id, tag_name, now)
                    )
                    tag_ids.append(tag_id)

        for sym in symbols:
            existing = conn.execute(
                "SELECT id FROM stocks WHERE symbol=? AND COALESCE(end_date, '')=COALESCE(?, '')",
                (sym, req.end_date)
            ).fetchone()
            if existing:
                skipped += 1
                stock_id = existing['id']
            else:
                stock_id = str(uuid.uuid4())
                conn.execute(
                    "INSERT INTO stocks (id, symbol, end_date, created_at) VALUES (?,?,?,?)",
                    (stock_id, sym, req.end_date, now)
                )
                imported += 1
                new_stock_ids.append(stock_id)

            # 关联 tags
            for tid in tag_ids:
                existing_link = conn.execute(
                    "SELECT 1 FROM stock_tags WHERE stock_id=? AND tag_id=?",
                    (stock_id, tid)
                ).fetchone()
                if not existing_link:
                    conn.execute(
                        "INSERT INTO stock_tags (stock_id, tag_id) VALUES (?,?)",
                        (stock_id, tid)
                    )

    return ImportResult(imported=imported, skipped=skipped, stock_ids=new_stock_ids)


@router.post("/analyze")
def trigger_batch_analyze(req: dict = None):
    if is_running():
        raise HTTPException(400, "分析正在进行中，请等待完成")

    with get_db() as conn:
        stock_ids = []
        if req and req.get('stock_ids'):
            stock_ids = req['stock_ids']
        elif req and req.get('tag'):
            rows = conn.execute("""
                SELECT st.stock_id FROM stock_tags st
                JOIN tags t ON t.id = st.tag_id
                WHERE t.name = ?
            """, (req['tag'],)).fetchall()
            stock_ids = [r['stock_id'] for r in rows]
        else:
            raise HTTPException(400, "请指定 stock_ids 或 tag")

        if not stock_ids:
            raise HTTPException(400, "没有找到待分析的股票")

    t = threading.Thread(
        target=analyze_stocks_sync,
        args=(stock_ids, str(DB_PATH), str(CHARTS_DIR)),
        daemon=True
    )
    t.start()

    return {"message": f"分析已启动，共 {len(stock_ids)} 只股票"}


@router.post("/{stock_id}/analyze")
def trigger_single_analyze(stock_id: str):
    with get_db() as conn:
        row = conn.execute("SELECT id, status FROM stocks WHERE id=?", (stock_id,)).fetchone()
        if not row:
            raise HTTPException(404, "股票不存在")
        # 卡在 analyzing/pending 状态的，先重置再重新触发
        if row['status'] in ('analyzing', 'pending'):
            conn.execute(
                "UPDATE stocks SET status='pending', error_message=NULL WHERE id=?",
                (stock_id,)
            )

    t = threading.Thread(
        target=analyze_stocks_sync,
        args=([stock_id], str(DB_PATH), str(CHARTS_DIR)),
        daemon=True
    )
    t.start()

    return {"message": "分析已启动"}


@router.post("/{stock_id}/stop-analyze")
def stop_single_analyze(stock_id: str):
    """停止正在进行的分析"""
    from web.services.analysis import stop_analysis, is_running
    
    with get_db() as conn:
        row = conn.execute("SELECT id, status FROM stocks WHERE id=?", (stock_id,)).fetchone()
        if not row:
            raise HTTPException(404, "股票不存在")
        
        # 只有分析中或待处理状态才能停止
        if row['status'] not in ('analyzing', 'pending'):
            return {"message": "当前不在分析中", "stopped": False}
        
        # 设置停止标志
        stop_analysis()
        
        # 更新股票状态为错误，标记为用户取消
        conn.execute(
            "UPDATE stocks SET status='error', error_message='用户取消' WHERE id=?",
            (stock_id,)
        )
    
    return {"message": "分析已停止", "stopped": True}


@router.post("/batch-update")
def batch_update_stocks(req: BatchUpdate):
    if not req.stock_ids:
        raise HTTPException(400, "stock_ids 不能为空")

    with get_db() as conn:
        # 验证所有 stock_id 存在
        placeholders = ','.join('?' * len(req.stock_ids))
        existing = conn.execute(
            f"SELECT id FROM stocks WHERE id IN ({placeholders})", req.stock_ids
        ).fetchall()
        existing_ids = {r['id'] for r in existing}
        missing = [sid for sid in req.stock_ids if sid not in existing_ids]
        if missing:
            raise HTTPException(404, f"股票不存在: {', '.join(missing[:3])}")

        now = datetime.now().isoformat()

        # 批量更新 end_date
        if req.end_date is not None:
            conn.execute(
                f"UPDATE stocks SET end_date=? WHERE id IN ({placeholders})",
                [req.end_date] + req.stock_ids
            )

        # 批量更新 tags
        if req.tags is not None:
            # 确保 tags 存在
            tag_ids = []
            for tag_name in req.tags:
                tag_name = tag_name.strip()
                if not tag_name:
                    continue
                tag_row = conn.execute("SELECT id FROM tags WHERE name=?", (tag_name,)).fetchone()
                if tag_row:
                    tag_ids.append(tag_row['id'])
                else:
                    tag_id = str(uuid.uuid4())
                    conn.execute(
                        "INSERT INTO tags (id, name, created_at) VALUES (?,?,?)",
                        (tag_id, tag_name, now)
                    )
                    tag_ids.append(tag_id)

            for stock_id in req.stock_ids:
                if req.tag_mode == "replace":
                    conn.execute("DELETE FROM stock_tags WHERE stock_id=?", (stock_id,))

                for tid in tag_ids:
                    existing_link = conn.execute(
                        "SELECT 1 FROM stock_tags WHERE stock_id=? AND tag_id=?",
                        (stock_id, tid)
                    ).fetchone()
                    if not existing_link:
                        conn.execute(
                            "INSERT INTO stock_tags (stock_id, tag_id) VALUES (?,?)",
                            (stock_id, tid)
                        )

    return {"message": f"已更新 {len(req.stock_ids)} 只股票"}


@router.get("/{stock_id}", response_model=StockDetail)
def get_stock(stock_id: str):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM stocks WHERE id=?", (stock_id,)).fetchone()
        if not row:
            raise HTTPException(404, "股票不存在")

        label_row = conn.execute(
            "SELECT * FROM labels WHERE stock_id=?", (stock_id,)
        ).fetchone()

        # 获取 tags
        tag_rows = conn.execute("""
            SELECT t.name FROM tags t
            JOIN stock_tags st ON st.tag_id = t.id
            WHERE st.stock_id = ?
        """, (stock_id,)).fetchall()
        tags = [t['name'] for t in tag_rows]

    score_card = None
    if row['score_card_json']:
        try:
            score_card = json.loads(row['score_card_json'])
        except (json.JSONDecodeError, TypeError):
            pass

    fundamentals = None
    if row['fundamental_json']:
        try:
            fundamentals = json.loads(row['fundamental_json'])
        except (json.JSONDecodeError, TypeError):
            pass

    label = None
    if label_row:
        label = {
            'dl_grade': label_row['dl_grade'],
            'dl_note': label_row['dl_note'],
            'pt_grade': label_row['pt_grade'],
            'pt_note': label_row['pt_note'],
            'lk_grade': label_row['lk_grade'],
            'lk_note': label_row['lk_note'],
            'sf_grade': label_row['sf_grade'],
            'sf_note': label_row['sf_note'],
            'ty_grade': label_row['ty_grade'],
            'ty_note': label_row['ty_note'],
            'dn_grade': label_row['dn_grade'],
            'dn_note': label_row['dn_note'],
            'verdict': label_row['verdict'],
            'reason': label_row['reason'],
        }

    # 评级优先级：品种库用纯算法，标注记录用人工标注优先
    is_universe = (row['watch_status'] or 'none') != 'none'
    if is_universe:
        eff_dl = row['dl_grade']
        eff_pt = row['pt_grade']
        eff_lk = row['lk_grade']
        eff_sf = row['sf_grade']
        eff_ty = row['ty_grade']
        eff_dn = row['dn_grade']
    else:
        eff_dl = (label_row['dl_grade'] if label_row and label_row['dl_grade'] else None) or row['dl_grade']
        eff_pt = (label_row['pt_grade'] if label_row and label_row['pt_grade'] else None) or row['pt_grade']
        eff_lk = (label_row['lk_grade'] if label_row and label_row['lk_grade'] else None) or row['lk_grade']
        eff_sf = (label_row['sf_grade'] if label_row and label_row['sf_grade'] else None) or row['sf_grade']
        eff_ty = (label_row['ty_grade'] if label_row and label_row['ty_grade'] else None) or row['ty_grade']
        eff_dn = (label_row['dn_grade'] if label_row and label_row['dn_grade'] else None) or row['dn_grade']

    # 仓位建议优先级：人工标注的verdict > 算法的position_size
    eff_position_size = (label_row['verdict'] if label_row and label_row['verdict'] else None) or row['position_size']
    # 结论优先级：人工标注的reason > 算法的conclusion
    eff_conclusion = (label_row['reason'] if label_row and label_row['reason'] else None) or row['conclusion']

    return StockDetail(
        id=row['id'],
        symbol=row['symbol'],
        symbol_name=row['symbol_name'],
        market=row['market'],
        end_date=row['end_date'],
        status=row['status'],
        error_message=row['error_message'],
        score_card=score_card,
        dl_grade=eff_dl,
        pt_grade=eff_pt,
        lk_grade=eff_lk,
        sf_grade=eff_sf,
        ty_grade=eff_ty,
        dn_grade=eff_dn,
        conclusion=eff_conclusion,
        position_size=eff_position_size,
        label=label,
        analyzed_at=row['analyzed_at'],
        updated_at=row['updated_at'] if 'updated_at' in row.keys() else None,
        kline_end_time=row['kline_end_time'] if 'kline_end_time' in row.keys() else None,
        tags=tags,
        fundamentals=fundamentals,
    )


@router.put("/{stock_id}")
def update_stock(stock_id: str, req: StockUpdate):
    with get_db() as conn:
        row = conn.execute("SELECT id FROM stocks WHERE id=?", (stock_id,)).fetchone()
        if not row:
            raise HTTPException(404, "股票不存在")

        if req.end_date is not None:
            conn.execute("UPDATE stocks SET end_date=?, updated_at=? WHERE id=?",
                         (req.end_date, datetime.now().isoformat(), stock_id))

        if req.tags is not None:
            # 替换式更新 tags
            conn.execute("DELETE FROM stock_tags WHERE stock_id=?", (stock_id,))
            now = datetime.now().isoformat()
            for tag_name in req.tags:
                tag_name = tag_name.strip()
                if not tag_name:
                    continue
                tag_row = conn.execute("SELECT id FROM tags WHERE name=?", (tag_name,)).fetchone()
                if tag_row:
                    tag_id = tag_row['id']
                else:
                    tag_id = str(uuid.uuid4())
                    conn.execute(
                        "INSERT INTO tags (id, name, created_at) VALUES (?,?,?)",
                        (tag_id, tag_name, now)
                    )
                conn.execute(
                    "INSERT INTO stock_tags (stock_id, tag_id) VALUES (?,?)",
                    (stock_id, tag_id)
                )
            # 更新 updated_at
            conn.execute("UPDATE stocks SET updated_at=? WHERE id=?",
                         (datetime.now().isoformat(), stock_id))
            sync_labels_to_csv(conn)

    return {"message": "已更新"}


@router.delete("/{stock_id}")
def delete_stock(stock_id: str):
    with get_db() as conn:
        row = conn.execute("SELECT id FROM stocks WHERE id=?", (stock_id,)).fetchone()
        if not row:
            raise HTTPException(404, "股票不存在")
        conn.execute("DELETE FROM labels WHERE stock_id=?", (stock_id,))
        conn.execute("DELETE FROM stock_tags WHERE stock_id=?", (stock_id,))
        conn.execute("DELETE FROM stocks WHERE id=?", (stock_id,))
    return {"message": "已删除"}


@router.get("/{stock_id}/chart")
def get_chart(stock_id: str):
    with get_db() as conn:
        row = conn.execute(
            "SELECT chart_path FROM stocks WHERE id=?", (stock_id,)
        ).fetchone()
    if not row or not row['chart_path']:
        raise HTTPException(404, "图表不存在")

    chart_path = Path(row['chart_path'])
    if not chart_path.exists():
        raise HTTPException(404, "图表文件不存在")

    return FileResponse(str(chart_path), media_type="image/png")


def _get_stock_tags_map(conn, stock_ids: list) -> dict:
    """批量获取股票的 tags，返回 {stock_id: [tag_name, ...]}"""
    if not stock_ids:
        return {}
    placeholders = ','.join('?' * len(stock_ids))
    rows = conn.execute(f"""
        SELECT st.stock_id, t.name
        FROM stock_tags st
        JOIN tags t ON t.id = st.tag_id
        WHERE st.stock_id IN ({placeholders})
    """, stock_ids).fetchall()

    result = {}
    for r in rows:
        result.setdefault(r['stock_id'], []).append(r['name'])
    return result


def _row_to_list_item(row, tags: list) -> StockListItem:
    return StockListItem(
        id=row['id'],
        symbol=row['symbol'],
        symbol_name=row['symbol_name'],
        market=row['market'],
        end_date=row['end_date'],
        status=row['status'],
        dl_grade=row['dl_grade'],
        pt_grade=row['pt_grade'],
        lk_grade=row['lk_grade'],
        sf_grade=row['sf_grade'],
        ty_grade=row['ty_grade'],
        dn_grade=row['dn_grade'],
        conclusion=row['conclusion'],
        position_size=row['position_size'],
        label_status=row['label_status'],
        analyzed_at=row['analyzed_at'],
        updated_at=row['updated_at'] if 'updated_at' in row.keys() else None,
        kline_end_time=row['kline_end_time'] if 'kline_end_time' in row.keys() else None,
        tags=tags,
    )
