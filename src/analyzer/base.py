"""
六维打分系统 - 基础数据结构

定义评分枚举、各维度Result数据类、分析配置和综合评分卡。
"""
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime
from typing import Optional


# ─── 评分枚举 ───

class GradeScore(Enum):
    """通用评分等级: S(优秀) / A(一般) / B(较差) / C(不满足)"""
    S = 4
    A = 3
    B = 2
    C = 1

    def __str__(self):
        return self.name

    def __ge__(self, other):
        if isinstance(other, GradeScore):
            return self.value >= other.value
        return NotImplemented

    def __gt__(self, other):
        if isinstance(other, GradeScore):
            return self.value > other.value
        return NotImplemented

    def __le__(self, other):
        if isinstance(other, GradeScore):
            return self.value <= other.value
        return NotImplemented

    def __lt__(self, other):
        if isinstance(other, GradeScore):
            return self.value < other.value
        return NotImplemented


class ReleaseLevel(Enum):
    """释放级别: 1st(最优) / 2nd(需等回踩) / 3rd(需等新结构)"""
    FIRST = 1
    SECOND = 2
    THIRD = 3

    def __str__(self):
        labels = {1: "1st", 2: "2nd", 3: "3rd"}
        return labels[self.value]


class PassFail(Enum):
    """DL独立结构专用: S(通过) / FAIL(不通过)"""
    S = "S"
    FAIL = "FAIL"

    def __str__(self):
        return self.value


# ─── 各维度分析结果 ───

@dataclass
class StructureResult:
    """DL 独立结构检测结果"""
    score: GradeScore = GradeScore.C
    passed: bool = False
    kline_count: int = 0
    range_high: float = 0.0
    range_low: float = 0.0
    range_pct: float = 0.0
    structure_start_idx: int = 0
    structure_end_idx: int = 0
    prior_trend_slope: float = 0.0     # 带符号归一化斜率: 负=下跌, 正=上涨
    structure_slope: float = 0.0       # 带符号归一化斜率: 负=右下倾, 正=右上倾
    flaws: list = field(default_factory=list)
    reasoning: list = field(default_factory=list)


@dataclass
class PlatformResult:
    """PT 平台位/颈线位检测结果"""
    score: GradeScore = GradeScore.C
    passed: bool = False

    # 激活平台（由DN方向决定）
    platform_price: float = 0.0
    platform_zone_high: float = 0.0
    platform_zone_low: float = 0.0
    platform_type: str = ""       # 'resistance' / 'support'
    touch_count: int = 0
    touch_points: list = field(default_factory=list)
    penetration_count: int = 0

    # 上平台（阻力位）
    resistance_price: float = 0.0
    resistance_zone_high: float = 0.0
    resistance_zone_low: float = 0.0
    resistance_touches: list = field(default_factory=list)
    resistance_touch_count: int = 0
    resistance_penetrations: int = 0
    resistance_shadow_penetrations: int = 0    # 影线穿越次数
    resistance_body_penetrations: int = 0      # 实体穿越次数
    resistance_post_pen_tests: int = 0         # 实体穿越后的有效测试次数
    resistance_score: GradeScore = GradeScore.C

    # 下平台（支撑位）
    support_price: float = 0.0
    support_zone_high: float = 0.0
    support_zone_low: float = 0.0
    support_touches: list = field(default_factory=list)
    support_touch_count: int = 0
    support_penetrations: int = 0
    support_shadow_penetrations: int = 0       # 影线穿越次数
    support_body_penetrations: int = 0         # 实体穿越次数
    support_post_pen_tests: int = 0            # 实体穿越后的有效测试次数
    support_score: GradeScore = GradeScore.C

    has_tail_energy: bool = False
    all_candidates: list = field(default_factory=list)
    reasoning: list = field(default_factory=list)


@dataclass
class ContourResult:
    """LK 轮廓质量评估结果"""
    score: GradeScore = GradeScore.C
    passed: bool = False
    quality_score: float = 0.0
    upper_smoothness: float = 0.0
    lower_smoothness: float = 0.0
    range_cv: float = 0.0
    abnormal_count: int = 0
    abnormal_ratio: float = 0.0
    width_pct: float = 0.0
    is_narrow: bool = False
    symmetry_score: float = 0.0       # 前后半段对称性 [0,1]
    tail_break: bool = False           # 尾部是否破位（突破前期低点）
    tail_break_pct: float = 0.0       # 尾部破位幅度%
    density_score: float = 0.0        # 中间段密集度 [0,1]
    reasoning: list = field(default_factory=list)


@dataclass
class SqueezeResult:
    """TY 统一区间检测结果"""
    score: GradeScore = GradeScore.C
    passed: bool = False
    squeeze_length: int = 0
    squeeze_start_idx: int = 0
    squeeze_end_idx: int = 0
    avg_range: float = 0.0
    avg_range_ratio: float = 0.0
    slope_pct: float = 0.0
    gap_to_trigger: int = 0
    interruptions: int = 0
    reasoning: list = field(default_factory=list)


@dataclass
class MomentumResult:
    """DN 动能分析结果"""
    score: GradeScore = GradeScore.C
    passed: bool = False
    direction: str = ""  # 'bullish' / 'bearish'
    trigger_idx: int = -1
    trigger_range: float = 0.0
    force_ratio: float = 0.0
    merged_count: int = 0
    broke_platform: bool = False
    volume_ratio: float = 0.0
    trigger_close: float = 0.0
    reasoning: list = field(default_factory=list)
    pending: bool = False  # 尚未出现突破K线


@dataclass
class ReleaseResult:
    """SF 释放级别评估结果（尾部向突破方向蹭的程度）"""
    score: ReleaseLevel = ReleaseLevel.THIRD
    passed: bool = False
    tail_drift_pct: float = 0.0       # 尾部方向性偏移百分比
    tail_length: int = 0              # 检测到的尾部长度（K线数）
    direction: str = ""               # 评估方向: bullish/bearish/unknown
    action_advice: str = ""
    reasoning: list = field(default_factory=list)


# ─── 综合评分卡 ───

@dataclass
class ScoreCard:
    """六维综合评分卡"""
    symbol: str = ""
    symbol_name: str = ""
    market: str = "cn"  # 'cn'=A股(只做多), 'us'=美股(可双边)
    analysis_time: Optional[datetime] = None
    data_start: Optional[datetime] = None
    data_end: Optional[datetime] = None
    total_klines: int = 0

    dl_result: Optional[StructureResult] = None
    pt_result: Optional[PlatformResult] = None
    lk_result: Optional[ContourResult] = None
    ty_result: Optional[SqueezeResult] = None
    dn_result: Optional[MomentumResult] = None
    sf_result: Optional[ReleaseResult] = None

    overall_passed: bool = False
    overall_grade: str = ""
    action_recommendation: str = ""
    position_size: str = ""          # "1R" / "0.5R" / "等待" / "不做"
    position_reason: str = ""        # 仓位判定理由
    conclusion_lines: list = field(default_factory=list)
    early_terminated: bool = False
    early_terminate_reason: str = ""


# ─── 分析配置 ───

@dataclass
class AnalyzerConfig:
    """所有量化阈值集中管理"""

    # DL 独立结构
    dl_min_klines: int = 90               # 最少K线数（硬门槛）
    dl_flat_slope_threshold: float = 0.30  # 盘整斜率阈值 (%/K线)
    dl_window_size: int = 20               # 滑动窗口大小
    dl_noise_tolerance: int = 15           # 盘整段内允许的趋势噪声窗口数
    dl_steep_decline_threshold: float = 0.50  # 前趋势急跌阈值
    dl_tilt_threshold: float = 0.08        # 结构右倾阈值
    dl_concentration_threshold: float = 0.05  # 筹码集中度阈值(std/mean)（仅记录）
    dl_max_drift_pct: float = 5.0              # 端点漂移阈值(%)，超过则收窄结构
    dl_max_range_pct: float = 7.0              # 结构最大振幅(%)，超过则收窄结构

    # PT 平台位
    pt_bin_width_atr_ratio: float = 0.1    # 直方图bin宽度 = ATR * ratio
    pt_touch_tolerance_atr_ratio: float = 0.15  # 触碰容忍带宽
    pt_min_touch_count: int = 3            # 最少测试次数（硬性要求）
    pt_min_touch_interval: int = 20        # 相邻触碰理想间隔K线数
    pt_tail_window: int = 10               # 尾部能量检测窗口
    pt_tail_energy_range_mult: float = 2.0  # 大K线判定倍数(vs ATR)
    pt_tail_energy_vol_mult: float = 2.0   # 放量判定倍数(vs 均量)
    pt_adjustment_min_bars: int = 8        # 第3次触碰前最小调整K线数
    pt_adjustment_distance_atr: float = 0.5  # 远离平台的最小距离(ATR倍数)

    # LK 轮廓
    lk_rolling_window: int = 10            # 上下轨滑动窗口
    lk_weight_smoothness: float = 0.40     # 平滑度权重
    lk_weight_cv: float = 0.35            # 均匀性权重
    lk_weight_abnormal: float = 0.25      # 异常K线权重
    lk_abnormal_std_mult: float = 2.0     # 异常K线判定(均值+N*标准差)
    lk_narrow_threshold: float = 0.03     # 窄结构阈值(振幅/均价)
    lk_narrow_penalty: float = 0.10       # 窄结构评分惩罚

    # TY 统一区间
    ty_squeeze_atr_ratio: float = 0.6      # 小K线判定(振幅 < ATR*ratio)
    ty_scan_window: int = 30               # 从尾部扫描的K线数
    ty_max_interruptions: int = 1          # 允许夹杂的非小K线数
    ty_max_gap_to_trigger: int = 1         # 与触发K线最大间距
    ty_slope_s_threshold: float = 0.01     # S级斜率阈值
    ty_slope_a_threshold: float = 0.02     # A级斜率阈值
    ty_slope_b_threshold: float = 0.03     # B级斜率阈值

    # DN 动能
    dn_force_ratio_s: float = 3.0          # S级力度比阈值
    dn_force_ratio_a: float = 2.0          # A级力度比阈值
    dn_force_ratio_b: float = 1.5          # B级力度比阈值
    dn_volume_ratio_s: float = 2.0         # S级放量阈值
    dn_max_merged: int = 3                 # 最大合并K线数

    # SF 释放级别（尾部向突破方向蹭的程度）
    sf_tail_drift_1st_max: float = 1.5    # 1st级最大尾部偏移(%)
    sf_tail_drift_2nd_max: float = 4.0    # 2nd级最大尾部偏移(%)
    # 超过2nd阈值 → 3rd
