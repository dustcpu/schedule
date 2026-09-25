# -*- coding: utf-8 -*-
"""CP-SAT 建模层：硬约束 + 软约束惩罚项。

硬约束（对应 v1 基线）：
  H1 每格唯一   每班每天每节恰好排一门（自习作为普通格参与）
  H2 周课时     每班每科节数 == input.xlsx 需求
  H3 教师冲突   同一教师同一天同一节最多带 1 个班
  H4 固定课     指定 (天,节) 强制为指定学科（班会/研究性学习/校本课）
  H5 连堂       连堂学科在其连堂日恰好排 N 个连续块，且该天该科节次必须落在块内
  H6 分科       由 input.xlsx 的科类 + 课程表决定各班的学科集合（数据层生效）
  H7 作息       节次→时钟由 config.compute_period_labels 计算（已修正 R2 午休缺失）

软约束（加权惩罚进目标函数，权重可在 hard_limits.json 的 soft_weights 配置）：
  S1 主科分散      语数英等主科每天不超过 2 节
  S2 体育不排首末  体育课不排第 1 节与当天最后一节
  S3 教师日均均衡  教师每日课时尽量接近其日均
  S4 首节主科      第 1 节优先排主科（奖励，负惩罚）
  S5 自习后置      自习尽量排在下午
"""
import math
from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Any

from ortools.sat.python import cp_model

from ..config import Config, NUM_DAYS, SELF_STUDY
from ..data.load import Problem

# 变量键：(班级ID, 学科, 天(1-based), 节次(1-based))
Key = Tuple[str, str, int, int]

PE_SUBJECT = "体育"
CORE_PER_DAY_LIMIT = 2  # S1：主科每天最多节数


@dataclass
class ModelBundle:
    model: cp_model.CpModel
    x: Dict[Key, Any]
    terms: List[Tuple[int, Any]] = field(default_factory=list)
    problem: Problem = None
    cfg: Config = None
    subjects_by_class: Dict[str, List[str]] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)

    def objective_expr(self):
        return sum(w * e for w, e in self.terms)


def build_model(p: Problem) -> ModelBundle:
    cfg = p.config
    model = cp_model.CpModel()
    periods = cfg.periods()                       # [1..8]
    days = list(range(1, NUM_DAYS + 1))           # [1..5]
    n_per = cfg.schedule.periods_per_day

    # ---------- 学科 / 课时 / 教师 索引 ----------
    subjects_by_class: Dict[str, List[str]] = {}
    req: Dict[Tuple[str, str], int] = {}
    teacher_of: Dict[Tuple[str, str], Any] = {}
    block_len_of: Dict[Tuple[str, str], int] = {}
    for c in p.courses:
        subjects_by_class.setdefault(c.class_id, []).append(c.subject)
        req[(c.class_id, c.subject)] = c.weekly
        teacher_of[(c.class_id, c.subject)] = c.teacher_id
        block_len_of[(c.class_id, c.subject)] = c.block_len

    # ---------- 决策变量 x[(班级, 学科, 天, 节)] ----------
    x: Dict[Key, Any] = {}
    for cid, subs in subjects_by_class.items():
        for s in subs:
            for d in days:
                for per in periods:
                    x[(cid, s, d, per)] = model.NewBoolVar(f"x_{cid}_{s}_{d}_{per}")

    warnings: List[str] = []

    # ---------- H1 每格唯一 ----------
    for cid, subs in subjects_by_class.items():
        for d in days:
            for per in periods:
                model.AddExactlyOne(x[(cid, s, d, per)] for s in subs)

    # ---------- H2 周课时 ----------
    for (cid, s), n in req.items():
        model.Add(sum(x[(cid, s, d, per)] for d in days for per in periods) == n)

    # ---------- H3 教师冲突 ----------
    by_teacher: Dict[str, List[Tuple[str, str]]] = {}
    for (cid, s), t in teacher_of.items():
        if t:
            by_teacher.setdefault(t, []).append((cid, s))
    for t, pairs in by_teacher.items():
        for d in days:
            for per in periods:
                model.AddAtMostOne(x[(cid, s, d, per)] for (cid, s) in pairs)

    # ---------- H4 固定课 ----------
    for fc in cfg.fixed_classes:
        if not (1 <= fc.day <= NUM_DAYS) or fc.period not in periods:
            warnings.append(f"固定课「{fc.subject}」的天/节次({fc.day},{fc.period})超出范围，已忽略")
            continue
        hit = 0
        for cid, subs in subjects_by_class.items():
            if fc.subject in subs:
                model.Add(x[(cid, fc.subject, fc.day, fc.period)] == 1)
                hit += 1
        # hit == 0 时不重复告警：数据层 validate.py 已统一提示「固定课未出现在课程表」

    # ---------- H5 连堂 ----------
    consec = cfg.consecutive
    for (cid, s), blen in block_len_of.items():
        if blen <= 1:
            continue
        day = consec.day_of(s)
        if day is None:
            warnings.append(
                f"班级 {cid} 的「{s}」设置了连堂，但该学科不在连堂规则内，已按普通课时排"
            )
            continue
        if not (1 <= day <= NUM_DAYS):
            continue
        max_start = n_per - blen + 1
        candidates = [p0 for p0 in range(1, max_start + 1)]
        allowed = [p0 for p0 in candidates if p0 in consec.allow_start_periods] or candidates

        B: Dict[int, Any] = {}
        for p0 in allowed:
            v = model.NewBoolVar(f"b_{cid}_{s}_{day}_{p0}")
            B[p0] = v
            for k in range(blen):
                if p0 + k in periods:
                    model.AddImplication(v, x[(cid, s, day, p0 + k)])
        model.Add(sum(B.values()) == max(1, consec.blocks_per_day))

        # 该天该科的每一节都必须落在某个块内（杜绝零星单节）
        for per in periods:
            covering = [B[p0] for p0 in allowed if p0 <= per <= p0 + blen - 1]
            if covering:
                model.Add(x[(cid, s, day, per)] <= sum(covering))
            else:
                model.Add(x[(cid, s, day, per)] == 0)

    # ---------- H5b 禁止相邻排课（杜绝"伪连堂"） ----------
    # 规格：一门课一周只允许一次连堂，且必须落在它的指定连堂日（由 H5 保证）。
    # 因此相邻排课的禁则要覆盖所有学科：
    #   · 非连堂学科（blen<=1、自习除外）：任何一天都不得相邻排课
    #   · 连堂学科（blen>1）：指定日交给 H5（恰好一个连堂块），
    #     其余各天一律不得相邻排课 —— 否则语数英会在别的天再长出一个连堂，
    #     违反"一周只有一次连堂"。
    for (cid, s), blen in block_len_of.items():
        if s == SELF_STUDY:
            continue  # 自习允许连续
        desig = consec.day_of(s) if blen > 1 else None
        for d in days:
            if desig is not None and d == desig:
                continue  # 指定日由 H5 处理
            for per in range(1, n_per):  # per 和 per+1 相邻
                model.Add(x[(cid, s, d, per)] + x[(cid, s, d, per + 1)] <= 1)

    # ---------- H5c 连堂学科：非指定日每天至多 cap 节 ----------
    # 规格：周课时扣掉连堂块之后，余下的节数应在其余各天均匀铺开，
    # 使课表呈"每天一节 + 指定日连堂"，而不是挤在少数几天。
    #
    # cap 按周课时推算，保证永远可行：
    #   cap = ceil((周课时 - 连堂块节数) / 非指定日天数)
    #   周课时 6、连堂 2、其余 4 天  -> cap = 1（每天恰好一节）
    #   周课时 7、连堂 2、其余 4 天  -> cap = 2（否则无解，故自动放宽并告警）
    for (cid, s), blen in block_len_of.items():
        if blen <= 1:
            continue
        desig = consec.day_of(s)
        if desig is None:
            continue  # 非连堂规则内的学科，H5 已告警并按普通课时处理
        other_days = [d for d in days if d != desig]
        if not other_days:
            continue
        rest = req[(cid, s)] - blen
        cap = max(1, math.ceil(rest / len(other_days)))
        if cap > 1:
            warnings.append(
                f"班级 {cid} 的「{s}」周课时 {req[(cid, s)]} 节，"
                f"扣除连堂 {blen} 节后仍有 {rest} 节，"
                f"无法做到其余每天至多 1 节（已放宽为每天至多 {cap} 节）"
            )
        for d in other_days:
            model.Add(sum(x[(cid, s, d, per)] for per in periods) <= cap)

    # ---------- 软约束 ----------
    terms: List[Tuple[int, Any]] = []
    w = cfg.soft
    core_subjects = set(consec.subjects)

    # S1 主科分散：主科每天不超过 CORE_PER_DAY_LIMIT 节
    if w.spread_core > 0:
        for cid, subs in subjects_by_class.items():
            for s in subs:
                if s not in core_subjects:
                    continue
                for d in days:
                    cnt = model.NewIntVar(0, n_per, f"cnt_{cid}_{s}_{d}")
                    model.Add(cnt == sum(x[(cid, s, d, per)] for per in periods))
                    over = model.NewIntVar(0, n_per, f"ovr_{cid}_{s}_{d}")
                    model.Add(over >= cnt - CORE_PER_DAY_LIMIT)
                    terms.append((w.spread_core, over))

    # S2 体育不排第 1 节 / 最后一节
    if w.pe_not_first_last > 0:
        last = n_per
        for cid, subs in subjects_by_class.items():
            if PE_SUBJECT not in subs:
                continue
            for d in days:
                terms.append((w.pe_not_first_last, x[(cid, PE_SUBJECT, d, 1)]))
                terms.append((w.pe_not_first_last, x[(cid, PE_SUBJECT, d, last)]))

    # S3 教师日均均衡：超出日均上限的部分计罚
    if w.teacher_balance > 0:
        for t, pairs in by_teacher.items():
            total = sum(req[(cid, s)] for (cid, s) in pairs)
            cap = max(1, math.ceil(total / NUM_DAYS))
            for d in days:
                daily = model.NewIntVar(0, n_per, f"tl_{t}_{d}")
                model.Add(daily == sum(x[(cid, s, d, per)] for (cid, s) in pairs for per in periods))
                over = model.NewIntVar(0, n_per, f"tov_{t}_{d}")
                model.Add(over >= daily - cap)
                terms.append((w.teacher_balance, over))

    # S4 首节主科（奖励 → 负惩罚）
    if w.core_morning_first > 0:
        for cid, subs in subjects_by_class.items():
            for s in subs:
                if s not in core_subjects:
                    continue
                for d in days:
                    terms.append((-w.core_morning_first, x[(cid, s, d, 1)]))

    # S5 自习后置：上午（<= 上午节数）的自习计罚
    if w.selfstudy_afternoon > 0:
        mp = cfg.schedule.morning_periods
        for cid, subs in subjects_by_class.items():
            if SELF_STUDY not in subs:
                continue
            for d in days:
                for per in periods:
                    if per <= mp:
                        terms.append((w.selfstudy_afternoon, x[(cid, SELF_STUDY, d, per)]))

    model.Minimize(sum(wt * e for wt, e in terms))

    return ModelBundle(
        model=model,
        x=x,
        terms=terms,
        problem=p,
        cfg=cfg,
        subjects_by_class=subjects_by_class,
        warnings=warnings,
    )
