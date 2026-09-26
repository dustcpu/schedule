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


def _calc_metrics(sol: Dict[Any, int], bundle: ModelBundle) -> Dict[str, float]:
    """计算方案的多维度指标。"""
    cfg = bundle.cfg
    x = bundle.x
    periods = cfg.periods()
    days = list(range(1, NUM_DAYS + 1))
    core = set(cfg.consecutive.subjects)
    mp = cfg.schedule.morning_periods
    n_per = cfg.schedule.periods_per_day

    # 1. 首节主科率
    first_total = first_core = 0
    # 2. 自习后置率
    ss_total = ss_pm = 0
    # 3. 下午主科比例（越低越好，主科尽量在上午）
    pm_total = pm_core = 0
    # 4. 主科分散违反（同一天同一班主科超过2节的次数）
    core_over = 0
    # 5. 体育首末节违反
    pe_violation = 0

    subjects_by_class: Dict[str, List[str]] = bundle.subjects_by_class
    # 按班按天统计
    class_day_subjects: Dict[Tuple[str, int], List[str]] = {}

    for k, var in x.items():
        cid, s, d, per = k
        if sol.get(k) != 1:
            continue
        class_day_subjects.setdefault((cid, d), []).append(s)

        if per == 1:
            first_total += 1
            if s in core:
                first_core += 1
        if s == SELF_STUDY:
            ss_total += 1
            if per > mp:
                ss_pm += 1
        if per > mp:
            pm_total += 1
            if s in core:
                pm_core += 1
        if s == PE_SUBJECT and (per == 1 or per == n_per):
            pe_violation += 1

    # 主科分散违反
    for (cid, d), subs in class_day_subjects.items():
        core_cnt = sum(1 for s in subs if s in core)
        if core_cnt > 2:
            core_over += core_cnt - 2

    # 6. 教师日均极差
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
    max_span = max(spans) if spans else 0
    avg_span = sum(spans) / len(spans) if spans else 0

    return {
        "first_core_rate": (first_core / first_total * 100) if first_total else 0,
        "selfstudy_pm_rate": (ss_pm / ss_total * 100) if ss_total else 0,
        "pm_core_rate": (pm_core / pm_total * 100) if pm_total else 0,
        "core_over": core_over,
        "pe_violation": pe_violation,
        "teacher_max_span": max_span,
        "teacher_avg_span": avg_span,
    }


def _describe(metrics: Dict[str, float], rank: Dict[str, int], total: int) -> str:
    """根据指标和排名生成特点描述。"""
    parts = []

    # 基础指标（一位小数，更精确）
    parts.append(f"首节主科 {metrics['first_core_rate']:.1f}%")
    parts.append(f"自习下午 {metrics['selfstudy_pm_rate']:.1f}%")
    parts.append(f"教师极差 {metrics['teacher_max_span']:.0f}节")
    parts.append(f"下午主科 {metrics['pm_core_rate']:.1f}%")

    # 突出特点（各维度排名）
    highlights = []
    rank_labels = {
        "first_core_rate": "首节主科",
        "selfstudy_pm_rate": "自习后置",
        "teacher_max_span_rev": "教师均衡",
        "pm_core_rate_rev": "主科集中上午",
        "core_over_rev": "主科分散",
        "penalty_rev": "软约束最优",
    }
    for key, label in rank_labels.items():
        r = rank.get(key, 99)
        if r == 1:
            highlights.append(f"{label}第1")
        elif r == 2 and total <= 5:
            highlights.append(f"{label}第2")

    if highlights:
        parts.append("★ " + "、".join(highlights[:3]))

    return "，".join(parts)


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

    # 计算每个方案的多维度指标
    all_metrics = []
    for sol, pen, st in raw:
        m = _calc_metrics(sol, bundle)
        m["penalty"] = pen
        all_metrics.append(m)

    # 计算每个指标的排名（1=最好）
    n = len(all_metrics)
    ranks = []
    for i in range(n):
        r = {}
        # 首节主科率：越高越好
        r["first_core_rate"] = sum(1 for j in range(n) if all_metrics[j]["first_core_rate"] > all_metrics[i]["first_core_rate"]) + 1
        # 自习后置率：越高越好
        r["selfstudy_pm_rate"] = sum(1 for j in range(n) if all_metrics[j]["selfstudy_pm_rate"] > all_metrics[i]["selfstudy_pm_rate"]) + 1
        # 下午主科率：越低越好
        r["pm_core_rate_rev"] = sum(1 for j in range(n) if all_metrics[j]["pm_core_rate"] < all_metrics[i]["pm_core_rate"]) + 1
        # 主科分散违反：越少越好
        r["core_over_rev"] = sum(1 for j in range(n) if all_metrics[j]["core_over"] < all_metrics[i]["core_over"]) + 1
        # 教师极差：越小越好
        r["teacher_max_span_rev"] = sum(1 for j in range(n) if all_metrics[j]["teacher_max_span"] < all_metrics[i]["teacher_max_span"]) + 1
        # 惩罚值：越低越好
        r["penalty_rev"] = sum(1 for j in range(n) if all_metrics[j]["penalty"] < all_metrics[i]["penalty"]) + 1
        ranks.append(r)

    # 综合评分：多维度加权（满分100）
    # 权重：首节主科20% + 自习后置20% + 教师均衡20% + 主科分散15% + 下午主科15% + 惩罚值10%
    plans: List[Plan] = []
    for i, (sol, pen, st) in enumerate(raw):
        m = all_metrics[i]
        r = ranks[i]
        n_plans = len(raw)

        # 每个维度按排名换算成分数（第1名100分，最后一名70分）
        def rank_score(rank):
            if n_plans <= 1:
                return 100.0
            return 100.0 - (rank - 1) / (n_plans - 1) * 30.0

        score = (
            rank_score(r["first_core_rate"]) * 0.20 +
            rank_score(r["selfstudy_pm_rate"]) * 0.20 +
            rank_score(r["teacher_max_span_rev"]) * 0.20 +
            rank_score(r["core_over_rev"]) * 0.15 +
            rank_score(r["pm_core_rate_rev"]) * 0.15 +
            rank_score(r["penalty_rev"]) * 0.10
        )
        score = round(score, 1)

        plans.append(Plan(
            index=i + 1,
            name=PLAN_NAMES[i % len(PLAN_NAMES)],
            score=score,
            note=_describe(m, r, n_plans),
            grid=_extract_grid(sol),
        ))

    # 按评分排序（高分在前）
    plans.sort(key=lambda p: -p.score)
    # 重新编号
    for i, p in enumerate(plans):
        p.index = i + 1

    if len(plans) < cfg.solver.min_plans:
        warnings.append(
            f"约束较严或时限较紧，仅生成 {len(plans)} 套方案（期望 {cfg.solver.min_plans} 套以上）"
        )

    return plans, warnings, last_status
