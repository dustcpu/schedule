# -*- coding: utf-8 -*-
"""求解层：多次求解生成多套方案（no-good cut），并给出评分与特征描述。

多解策略：每求出一个解，就加一条 no-good 约束禁止完全复刻，再求下一个，
直到凑够目标套数（协议要求 3-8 套）或无解/超时。
"""
from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Any, Optional

from ortools.sat.python import cp_model

from ..config import NUM_DAYS, SELF_STUDY
from ..data.load import Problem
from .model import ModelBundle, PE_SUBJECT

# (班级ID, 天, 节) -> 学科
Grid = Dict[Tuple[str, int, int], str]

PLAN_NAMES = [
    "均衡方案", "主科优先方案", "自习后置方案",
    "教师均衡方案", "紧凑方案", "宽松方案", "错峰方案", "分散方案",
]


@dataclass
class Plan:
    index: int
    name: str
    score: float
    note: str
    grid: Grid = field(default_factory=dict)


def _extract_grid(sol: Dict[Any, int]) -> Grid:
    grid: Grid = {}
    for (cid, s, d, per), v in sol.items():
        if v == 1:
            grid[(cid, d, per)] = s
    return grid


def _describe(sol: Dict[Any, int], bundle: ModelBundle) -> str:
    """生成方案特征描述（用于 plans[].note）。"""
    cfg = bundle.cfg
    x = bundle.x
    periods = cfg.periods()
    days = list(range(1, NUM_DAYS + 1))
    core = set(cfg.consecutive.subjects)
    mp = cfg.schedule.morning_periods

    # 首节主科占比
    first_total = first_core = 0
    # 自习在下午占比
    ss_total = ss_pm = 0
    for k, var in x.items():
        cid, s, d, per = k
        if sol.get(k) != 1:
            continue
        if per == 1:
            first_total += 1
            if s in core:
                first_core += 1
        if s == SELF_STUDY:
            ss_total += 1
            if per > mp:
                ss_pm += 1

    parts = []
    if first_total:
        parts.append(f"首节主科 {round(first_core / first_total * 100)}%")
    if ss_total:
        parts.append(f"自习在下午 {round(ss_pm / ss_total * 100)}%")

    # 教师日均极差
    teacher_of = {(c.class_id, c.subject): c.teacher_id for c in bundle.problem.courses}
    teacher_day: Dict[str, Dict[int, int]] = {}
    for k, var in x.items():
        cid, s, d, per = k
        if sol.get(k) != 1:
            continue
        t = teacher_of.get((cid, s))
        if t:
            teacher_day.setdefault(t, {}).setdefault(d, 0)
            teacher_day[t][d] += 1
    spans = []
    for t, dd in teacher_day.items():
        vals = [dd.get(d, 0) for d in days]
        spans.append(max(vals) - min(vals))
    if spans:
        parts.append(f"教师日均极差 {max(spans)} 节")

    return "，".join(parts) if parts else "满足全部硬约束"


def solve_plans(bundle: ModelBundle) -> Tuple[List[Plan], List[str], str]:
    """返回 (方案列表, 求解过程 warnings, 最终状态字符串)。"""
    cfg = bundle.cfg
    warnings: List[str] = list(bundle.warnings)
    raw: List[Tuple[Dict[Any, int], float, str]] = []

    target = max(cfg.solver.min_plans, min(cfg.solver.max_plans, cfg.solver.num_plans))
    last_status = "UNKNOWN"

    for i in range(cfg.solver.max_plans):
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = float(cfg.solver.max_time_seconds)
        solver.parameters.num_search_workers = int(cfg.solver.workers)

        status = solver.Solve(bundle.model)
        if status == cp_model.OPTIMAL:
            last_status = "OPTIMAL"
        elif status == cp_model.FEASIBLE:
            last_status = "FEASIBLE"
        elif status == cp_model.INFEASIBLE:
            last_status = "INFEASIBLE"
            break
        else:  # UNKNOWN / MODEL_INVALID
            last_status = "UNKNOWN"
            break

        sol = {k: solver.Value(v) for k, v in bundle.x.items()}
        pen = solver.ObjectiveValue()
        raw.append((sol, pen, last_status))

        # no-good：禁止再次得到完全相同的解
        ones = [bundle.x[k] for k, v in sol.items() if v == 1]
        if ones:
            bundle.model.Add(sum(ones) <= len(ones) - 1)

        if len(raw) >= target:
            break

    if not raw:
        return [], warnings, last_status

    # 评分：以本批最优惩罚为 100 分基准，最差不低于 70
    pens = [r[1] for r in raw]
    min_pen, max_pen = min(pens), max(pens)
    span = max(1.0, max_pen - min_pen)

    plans: List[Plan] = []
    for i, (sol, pen, st) in enumerate(raw, start=1):
        score = 100.0 - (pen - min_pen) / span * 30.0
        score = round(max(0.0, min(100.0, score)), 1)
        plans.append(Plan(
            index=i,
            name=PLAN_NAMES[(i - 1) % len(PLAN_NAMES)],
            score=score,
            note=_describe(sol, bundle),
            grid=_extract_grid(sol),
        ))

    if len(plans) < cfg.solver.min_plans:
        warnings.append(
            f"约束较严或时限较紧，仅生成 {len(plans)} 套方案（期望 {cfg.solver.min_plans} 套以上）"
        )

    return plans, warnings, last_status
