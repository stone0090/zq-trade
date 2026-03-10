"""标签管理 API"""
import uuid
from datetime import datetime
from fastapi import APIRouter, HTTPException

from web.database import get_db
from web.models import TagCreate, TagResponse

router = APIRouter(prefix="/api/tags", tags=["tags"])


@router.get("", response_model=list[TagResponse])
def list_tags():
    with get_db() as conn:
        rows = conn.execute("""
            SELECT t.id, t.name, t.created_at,
                   COUNT(st.stock_id) as stock_count
            FROM tags t
            LEFT JOIN stock_tags st ON st.tag_id = t.id
            GROUP BY t.id
            ORDER BY t.created_at DESC
        """).fetchall()
    return [TagResponse(
        id=r['id'], name=r['name'],
        stock_count=r['stock_count'], created_at=r['created_at']
    ) for r in rows]


@router.post("", response_model=TagResponse)
def create_tag(req: TagCreate):
    name = req.name.strip()
    if not name:
        raise HTTPException(400, "标签名不能为空")

    with get_db() as conn:
        existing = conn.execute("SELECT id FROM tags WHERE name=?", (name,)).fetchone()
        if existing:
            raise HTTPException(400, "标签名已存在")

        tag_id = str(uuid.uuid4())
        now = datetime.now().isoformat()
        conn.execute(
            "INSERT INTO tags (id, name, created_at) VALUES (?,?,?)",
            (tag_id, name, now)
        )

    return TagResponse(id=tag_id, name=name, stock_count=0, created_at=now)


@router.put("/{tag_id}", response_model=TagResponse)
def rename_tag(tag_id: str, req: TagCreate):
    name = req.name.strip()
    if not name:
        raise HTTPException(400, "标签名不能为空")

    with get_db() as conn:
        row = conn.execute("SELECT * FROM tags WHERE id=?", (tag_id,)).fetchone()
        if not row:
            raise HTTPException(404, "标签不存在")

        # 检查重名
        dup = conn.execute(
            "SELECT id FROM tags WHERE name=? AND id!=?", (name, tag_id)
        ).fetchone()
        if dup:
            raise HTTPException(400, "标签名已存在")

        conn.execute("UPDATE tags SET name=? WHERE id=?", (name, tag_id))

        count = conn.execute(
            "SELECT COUNT(*) as c FROM stock_tags WHERE tag_id=?", (tag_id,)
        ).fetchone()['c']

    return TagResponse(
        id=tag_id, name=name,
        stock_count=count, created_at=row['created_at']
    )


@router.delete("/{tag_id}")
def delete_tag(tag_id: str):
    with get_db() as conn:
        row = conn.execute("SELECT id FROM tags WHERE id=?", (tag_id,)).fetchone()
        if not row:
            raise HTTPException(404, "标签不存在")
        # 只删标签和关联，不删股票
        conn.execute("DELETE FROM stock_tags WHERE tag_id=?", (tag_id,))
        conn.execute("DELETE FROM tags WHERE id=?", (tag_id,))
    return {"message": "标签已删除"}
