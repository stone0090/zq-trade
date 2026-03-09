"""ScoreCard 序列化工具 - 将分析结果转为可 JSON 序列化的格式"""
from datetime import datetime
from dataclasses import asdict
from enum import Enum

from core.types import GradeScore


def _serialize(obj):
    """递归序列化 dataclass / enum / datetime / numpy"""
    if obj is None:
        return None
    if isinstance(obj, Enum):
        return obj.name if isinstance(obj, GradeScore) else str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, (bool,)):
        return obj
    # numpy 类型处理
    try:
        import numpy as np
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            return float(obj)
        if isinstance(obj, np.bool_):
            return bool(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
    except ImportError:
        pass
    if isinstance(obj, (list, tuple)):
        return [_serialize(v) for v in obj]
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    if hasattr(obj, '__dataclass_fields__'):
        return {k: _serialize(v) for k, v in asdict(obj).items()}
    return obj


def scorecard_to_dict(card) -> dict:
    """将 ScoreCard 转为可 JSON 序列化的 dict"""
    d = {}
    for field_name in card.__dataclass_fields__:
        val = getattr(card, field_name)
        d[field_name] = _serialize(val)
    return d


def extract_grades(card_dict: dict) -> dict:
    """从 ScoreCard dict 提取各维度 grade 字符串"""
    grades = {}

    dl = card_dict.get('dl_result')
    if dl:
        score = dl.get('score')
        grades['dl_grade'] = score if score else None
    else:
        grades['dl_grade'] = None

    pt = card_dict.get('pt_result')
    if pt:
        grades['pt_grade'] = pt.get('score')
    else:
        grades['pt_grade'] = None

    lk = card_dict.get('lk_result')
    if lk:
        grades['lk_grade'] = lk.get('score')
    else:
        grades['lk_grade'] = None

    sf = card_dict.get('sf_result')
    if sf:
        score_val = sf.get('score')
        grades['sf_grade'] = str(score_val) if score_val else None
    else:
        grades['sf_grade'] = None

    ty = card_dict.get('ty_result')
    if ty:
        if ty.get('pending'):
            grades['ty_grade'] = '待定'
        else:
            grades['ty_grade'] = ty.get('score')
    else:
        grades['ty_grade'] = None

    dn = card_dict.get('dn_result')
    if dn:
        if dn.get('pending'):
            grades['dn_grade'] = '待定'
        else:
            grades['dn_grade'] = dn.get('score')
    else:
        grades['dn_grade'] = None

    return grades
