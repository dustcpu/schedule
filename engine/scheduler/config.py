# -*- coding: utf-8 -*-
"""配置层：作息、固定课、连堂规则、软约束权重、求解参数。

默认值与外壳 Rust `Config::default()` 严格对齐（src-tauri/src/main.rs 行 80-137），
保证外壳不传 hard_limits.json 时，内核算出的作息与 UI 设置页完全一致。

同时实现修正 R2 的节次时间标签算法：午休后必须重置到 afternoon_start。
"""
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Tuple

# 一周上课日（v1 只排周一至周五，不含早读/晚自习）
DAYS: List[str] = ["周一", "周二", "周三", "周四", "周五"]
NUM_DAYS = len(DAYS)

# 自习作为普通格参与排课（方向共识：自习作为普通格）
SELF_STUDY = "自习"


def _norm_header(s: Any) -> str:
    """表头归一化：去空格/全角空格，转字符串。"""
    return str(s).strip().replace("　", "") if s is not None else ""


# ---------------------------------------------------------------- 作息
@dataclass
class ScheduleRule:
    periods_per_day: int = 8
    period_minutes: int = 40
    morning_start: str = "08:00"
    morning_end: str = "12:20"
    afternoon_start: str = "14:30"
    afternoon_end: str = "16:55"
    long_break_after_period: int = 3
    long_break_minutes: int = 30
    eye_break_after_period: int = 6
    eye_break_minutes: int = 15
    default_break_minutes: int = 10
    morning_periods: int = 5  # 上午节数，决定连堂可放置范围
    # 体育活动：作为附加行显示在正课之后（不参与排课、不占 grid_size）。
    # 关闭后置 False，课表只输出 periods_per_day 行。
    sports_activity: bool = True
    activity_minutes: int = 40

    @staticmethod
    def from_dict(d: Dict[str, Any], base: "ScheduleRule") -> "ScheduleRule":
        s = ScheduleRule(**{**base.__dict__})
        if not isinstance(d, dict):
            return s
        for k, v in d.items():
            if hasattr(s, k) and v is not None:
                setattr(s, k, v)
        return s


def _parse_tm(t: str) -> int:
    """'HH:MM' → 当日分钟数。"""
    h, m = str(t).split(":")
    return int(h) * 60 + int(m)


def _fmt_tm(x: int) -> str:
    """当日分钟数 → 'HH:MM'。"""
    return f"{x // 60:02d}:{x % 60:02d}"


def compute_period_labels(s: ScheduleRule) -> List[str]:
    """计算每节的时钟标签。

    修正 mock_engine.py 的 R2 缺陷：上午排完后必须重置到 afternoon_start，
    否则午休缺口丢失、下午节次整体前移约 2 小时。
    """
    labels: List[str] = []
    cur = _parse_tm(s.morning_start)
    for p in range(1, s.periods_per_day + 1):
        # 关键：第 (morning_periods+1) 节开始进入下午，时钟重置到 afternoon_start
        if p == s.morning_periods + 1:
            cur = _parse_tm(s.afternoon_start)
        start = cur
        end = cur + s.period_minutes
        labels.append(f"第{p}节 {_fmt_tm(start)}-{_fmt_tm(end)}")
        cur = end
        if p < s.periods_per_day:
            if p == s.long_break_after_period:
                cur += s.long_break_minutes
            elif p == s.eye_break_after_period:
                cur += s.eye_break_minutes
            else:
                cur += s.default_break_minutes
    return labels


def compute_activity_label(s: ScheduleRule) -> str:
    """体育活动附加行的标签（节次 + 时钟），由作息推算而非硬编码。

    默认 8 节制下推出「第9节 17:05-17:45」，与改造前逐字一致。
    """
    labels = compute_period_labels(s)
    last_end = labels[-1].split(" ")[1].split("-")[1]   # 如 "16:55"
    start = _parse_tm(last_end) + s.default_break_minutes
    end = start + s.activity_minutes
    return f"第{s.periods_per_day + 1}节 {_fmt_tm(start)}-{_fmt_tm(end)}"


# ---------------------------------------------------------------- 固定课 / 连堂
@dataclass
class FixedClass:
    day: int        # 1=周一 ... 5=周五
    period: int     # 1..periods_per_day
    subject: str
    note: str = ""


@dataclass
class ConsecutiveRule:
    """连堂（语数英）：各自在指定天排若干 2 节连堂块。"""
    subjects: List[str] = field(default_factory=lambda: ["语文", "数学", "英语"])
    math_day: int = 2
    chinese_day: int = 3
    english_day: int = 4
    block_len: int = 2       # 每块连堂节数
    blocks_per_day: int = 1  # 每班每天连堂块数（方向共识：一个班一块，另一班错开）
    # 连堂块允许的起始节次：只允许第 1 节或第 4 节起，即连堂只能落在
    # 【1-2 节】或【4-5 节】两个位置。
    # 依据：一位老师通常带 2 个班，一个班排上午 1-2 节，另一个班排上午 4-5 节，
    # 两个班错开、便于教师连堂授课。不允许 2-3、3-4 等中间位置。
    allow_start_periods: List[int] = field(default_factory=lambda: [1, 4])
    # 仅语数英允许连堂（其他学科即使填了连堂节数也强制为0，且禁止相邻排课）
    only_core_subjects: bool = True

    def day_of(self, subject: str) -> Optional[int]:
        """返回该学科的连堂日（1-based），非连堂学科返回 None。"""
        return {
            "数学": self.math_day,
            "语文": self.chinese_day,
            "英语": self.english_day,
        }.get(subject)


DEFAULT_FIXED_CLASSES: List[FixedClass] = [
    FixedClass(day=1, period=1, subject="班会", note="由班主任上课"),
    FixedClass(day=1, period=8, subject="研究性学习", note=""),
    FixedClass(day=4, period=8, subject="校本课", note=""),
]


def fixed_classes_from_list(items: List[Dict[str, Any]],
                            base: List[FixedClass]) -> List[FixedClass]:
    if not items:
        return list(base)
    out: List[FixedClass] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        try:
            out.append(FixedClass(
                day=int(it.get("day", 0)),
                period=int(it.get("period", 0)),
                subject=str(it.get("subject", "")).strip(),
                note=str(it.get("note", "") or ""),
            ))
        except (TypeError, ValueError):
            continue
    return out or list(base)


def consecutive_from_dict(d: Dict[str, Any], base: ConsecutiveRule) -> ConsecutiveRule:
    c = ConsecutiveRule(**{
        "subjects": list(base.subjects),
        "math_day": base.math_day,
        "chinese_day": base.chinese_day,
        "english_day": base.english_day,
        "block_len": base.block_len,
        "blocks_per_day": base.blocks_per_day,
        "allow_start_periods": list(base.allow_start_periods),
        "only_core_subjects": base.only_core_subjects,
    })
    if not isinstance(d, dict):
        return c
    for k in ("math_day", "chinese_day", "english_day", "block_len", "blocks_per_day"):
        if d.get(k) is not None:
            try:
                setattr(c, k, int(d[k]))
            except (TypeError, ValueError):
                pass
    if d.get("only_core_subjects") is not None:
        c.only_core_subjects = bool(d["only_core_subjects"])
    if isinstance(d.get("subjects"), list):
        c.subjects = [str(x).strip() for x in d["subjects"] if str(x).strip()]
    return c


# ---------------------------------------------------------------- 软约束权重
@dataclass
class SoftWeights:
    """软约束权重：越大越重要；置 0 即关闭该条。"""
    spread_core: int = 3        # 主科（语数英）尽量分散到不同天
    pe_not_first_last: int = 2  # 体育不排第 1 节 / 当天最后一节
    teacher_balance: int = 2    # 教师每日课时均衡（极差惩罚）
    core_morning_first: int = 1  # 第 1 节优先排主科（奖励，负惩罚）
    selfstudy_afternoon: int = 1  # 自习尽量排在下午

    @staticmethod
    def from_dict(d: Dict[str, Any], base: "SoftWeights") -> "SoftWeights":
        w = SoftWeights(**{**base.__dict__})
        if isinstance(d, dict):
            for k, v in d.items():
                if hasattr(w, k) and v is not None:
                    try:
                        setattr(w, k, int(v))
                    except (TypeError, ValueError):
                        pass
        return w


# ---------------------------------------------------------------- 求解参数
@dataclass
class SolverConfig:
    max_time_seconds: int = 30      # 每次求解时限（远小于外壳 10 分钟总超时）
    num_plans: int = 5              # 目标方案数（协议要求 3-8）
    min_plans: int = 3
    max_plans: int = 8
    workers: int = 8
    self_study_fill: bool = True    # 未指定自习课时时，自动用剩余格子补自习

    @staticmethod
    def from_dict(d: Dict[str, Any], base: "SolverConfig") -> "SolverConfig":
        c = SolverConfig(**{**base.__dict__})
        if isinstance(d, dict):
            for k, v in d.items():
                if hasattr(c, k) and v is not None:
                    setattr(c, k, v)
        return c


# ---------------------------------------------------------------- 方案权重档位
# 每套方案用一套不同的软约束权重求解，让 3-8 套方案真正有所差异
# （协议 §4.1 要求「3-8 套不同的可行方案」）。
#
# 设计原则：
#   1. 主导项拉到默认的 10-15 倍，否则求解器不会为该维度真正让步；
#   2. 非主导项降到 0-2，否则所有档位都被默认权重拉回同一片解空间；
#   3. 第一档 = 默认权重，作为「标准课表」参照。
PLAN_PROFILES: List[Tuple[str, Dict[str, int]]] = [
    ("均衡方案", {
        "spread_core": 3, "pe_not_first_last": 2, "teacher_balance": 2,
        "core_morning_first": 1, "selfstudy_afternoon": 1}),
    ("主科优先方案", {
        "spread_core": 5, "pe_not_first_last": 1, "teacher_balance": 1,
        "core_morning_first": 12, "selfstudy_afternoon": 1}),
    ("自习后置方案", {
        "spread_core": 2, "pe_not_first_last": 1, "teacher_balance": 1,
        "core_morning_first": 2, "selfstudy_afternoon": 12}),
    ("教师均衡方案", {
        "spread_core": 2, "pe_not_first_last": 1, "teacher_balance": 15,
        "core_morning_first": 0, "selfstudy_afternoon": 0}),
    ("分散方案", {
        "spread_core": 15, "pe_not_first_last": 8, "teacher_balance": 1,
        "core_morning_first": 0, "selfstudy_afternoon": 0}),
    ("体育错峰方案", {
        "spread_core": 2, "pe_not_first_last": 15, "teacher_balance": 3,
        "core_morning_first": 2, "selfstudy_afternoon": 0}),
]


def resolve_profiles(target: int) -> List[Tuple[str, Dict[str, int]]]:
    """取前 target 套权重档位；档位不够时循环复用（靠 random_seed 与汉明距离兜底区分）。"""
    n = len(PLAN_PROFILES)
    if not n:
        return []
    out = list(PLAN_PROFILES[:target])
    i = 0
    while len(out) < target:
        out.append(PLAN_PROFILES[i % n])
        i += 1
    return out


# ---------------------------------------------------------------- 汇总配置
@dataclass
class Config:
    schedule: ScheduleRule = field(default_factory=ScheduleRule)
    fixed_classes: List[FixedClass] = field(default_factory=lambda: list(DEFAULT_FIXED_CLASSES))
    consecutive: ConsecutiveRule = field(default_factory=ConsecutiveRule)
    soft: SoftWeights = field(default_factory=SoftWeights)
    solver: SolverConfig = field(default_factory=SolverConfig)
    days: List[str] = field(default_factory=lambda: list(DAYS))

    def periods(self) -> List[int]:
        return list(range(1, self.schedule.periods_per_day + 1))

    def period_labels(self) -> List[str]:
        return compute_period_labels(self.schedule)

    def activity_label(self) -> str:
        """体育活动附加行标签；由作息推算，不再硬编码。"""
        return compute_activity_label(self.schedule)

    def grid_size(self) -> int:
        """每班每周总格数。"""
        return self.schedule.periods_per_day * NUM_DAYS


def load_from_hard_limits(hl: Dict[str, Any]) -> Config:
    """用外壳写入的 hard_limits.json 覆盖默认配置。

    协议 §3 只列了 input.xlsx / requirements.txt，但外壳实际会写 hard_limits.json
    （main.rs 行 315-320，即 R1 协议漂移）。内核采取兼容策略：
    有则覆盖，无则用默认值（与 Rust Config::default 一致）。
    """
    base = Config()
    if not isinstance(hl, dict):
        return base
    base.schedule = ScheduleRule.from_dict(hl.get("schedule", {}), ScheduleRule())
    base.fixed_classes = fixed_classes_from_list(hl.get("fixed_classes", []),
                                                 DEFAULT_FIXED_CLASSES)
    base.consecutive = consecutive_from_dict(hl.get("consecutive", {}), ConsecutiveRule())
    base.soft = SoftWeights.from_dict(hl.get("soft_weights", {}), SoftWeights())
    base.solver = SolverConfig.from_dict(hl.get("solver", {}), SolverConfig())
    return base
