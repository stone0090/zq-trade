"""Pydantic 请求/响应模型"""
from pydantic import BaseModel
from typing import Optional, List


# ─── 批次 ───

class BatchCreate(BaseModel):
    name: str = ""
    symbols: List[str]
    end_date: Optional[str] = None


class BatchResponse(BaseModel):
    id: str
    name: str
    created_at: str
    status: str
    total_count: int
    completed_count: int
    labeled_count: int


class BatchProgress(BaseModel):
    status: str
    total_count: int
    completed_count: int


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


# ─── 股票详情 ───

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
