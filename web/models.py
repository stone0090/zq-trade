"""Pydantic 请求/响应模型"""
from pydantic import BaseModel
from typing import Optional, List


# ─── 股票导入 ───

class StockImport(BaseModel):
    symbols: List[str]
    end_date: Optional[str] = None
    tags: Optional[List[str]] = None  # 标签名列表


class StockUpdate(BaseModel):
    end_date: Optional[str] = None
    tags: Optional[List[str]] = None


class BatchUpdate(BaseModel):
    stock_ids: List[str]
    end_date: Optional[str] = None
    tags: Optional[List[str]] = None
    tag_mode: str = "replace"  # "replace" 全量替换 | "add" 追加标签


class ImportResult(BaseModel):
    imported: int
    skipped: int
    stock_ids: List[str]


# ─── 标签 ───

class TagCreate(BaseModel):
    name: str


class TagResponse(BaseModel):
    id: str
    name: str
    stock_count: int
    created_at: str


# ─── 分析进度 ───

class AnalysisProgress(BaseModel):
    running: bool
    total: int
    completed: int
    current_symbol: Optional[str] = None


# ─── 股票列表项 ───

class StockListItem(BaseModel):
    id: str
    symbol: str
    symbol_name: str
    market: str
    end_date: Optional[str]
    status: str
    dl_grade: Optional[str]
    pt_grade: Optional[str]
    lk_grade: Optional[str]
    sf_grade: Optional[str]
    ty_grade: Optional[str]
    dn_grade: Optional[str]
    conclusion: Optional[str]
    position_size: Optional[str]
    label_status: str  # "labeled" | "unlabeled"
    analyzed_at: Optional[str]
    updated_at: Optional[str]
    tags: List[str] = []

class StockDetail(BaseModel):
    id: str
    symbol: str
    symbol_name: str
    market: str
    end_date: Optional[str]
    status: str
    error_message: Optional[str]
    score_card: Optional[dict]
    dl_grade: Optional[str]
    pt_grade: Optional[str]
    lk_grade: Optional[str]
    sf_grade: Optional[str]
    ty_grade: Optional[str]
    dn_grade: Optional[str]
    conclusion: Optional[str]
    position_size: Optional[str]
    label: Optional[dict]
    analyzed_at: Optional[str]
    updated_at: Optional[str]
    tags: List[str] = []
    fundamentals: Optional[dict] = None


# ─── 标注 ───

class LabelUpsert(BaseModel):
    dl_grade: Optional[str] = None
    dl_note: str = ""
    pt_grade: Optional[str] = None
    pt_note: str = ""
    lk_grade: Optional[str] = None
    lk_note: str = ""
    sf_grade: Optional[str] = None
    sf_note: str = ""
    ty_grade: Optional[str] = None
    ty_note: str = ""
    dn_grade: Optional[str] = None
    dn_note: str = ""
    verdict: Optional[str] = None
    reason: str = ""
