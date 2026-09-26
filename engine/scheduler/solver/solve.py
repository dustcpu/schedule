# -*- coding: utf-8 -*-
"""求解层：多次求解生成多套方案（no-good cut），并给出评分与特征描述。

多解策略：每求出一个解，就加一条 no-good 约束禁止完全复刻，再求下一个，
直到凑够目标套数（协议要求 3-8 套）或无解/超时。
"""
import time
from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Any, Optional

from ortools.sat.python import cp_model

from ..config import NUM_DAYS, SELF_STUDY, resolve_profiles
from ..data.load import Problem
from ..score import absolute_score
from .model import ModelBundle, PE_SUBJECT, apply_objective

# (班级ID, 天, 节) -> 学科
Grid = Dict[Tuple[str, int, int], str]

# 兜底：resolve_profiles 异常返回空时才用到
PLAN_NAMES = [
    "均衡方案", "主科优先方案", "自习后置方案",
    "教师均衡方案", "分散方案", "体育错峰方案", "紧凑方案", "宽松方案",
]

# 全局求解预算（秒）。外壳超时是 10 分钟（main.rs ENGINE_TIMEOUT），
# 这里留约 3 分钟给导出与进程退出，避免被强杀。
GLOBAL_BUDGET = 420.0


@dataclass
class Plan:
    index: int
    name: str
    score: float
    note: str
    grid: Grid = field(default_factory=dict)
    profile: str = ""   # 该方案所用的权重档位名，保证「名副其实」


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
    pe_total = 0      # 体育课总节数（用于判断该维度是否适用）

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
        if s == PE_SUBJECT:
            pe_total += 1
            if per == 1 or per == n_per:
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
        "pe_total": pe_total,
        "ss_total": ss_total,
        "teacher_max_span": max_span,
        "teacher_avg_span": avg_span,
    }


def _describe(metrics: Dict[str, float], sub: Dict[str, float]) -> str:
    """根据**绝对子分**生成特点描述。

    此前用「相对排名」生成亮点，方案指标相同时会全部输出一样的亮点；
    改用绝对子分后，亮点反映该方案真实的长板。
    """
    parts = [
        f"首节主科 {metrics['first_core_rate']:.1f}%",
        f"自习下午 {metrics['selfstudy_pm_rate']:.1f}%",
        f"教师极差 {metrics['teacher_max_span']:.0f}节",
        f"下午主科 {metrics['pm_core_rate']:.1f}%",
    ]
    tops = sorted(((v, k) for k, v in sub.items() if v is not None),
                  reverse=True)[:2]
    if tops:
        parts.append("★ " + "、".join(f"{k} {v:.0f}" for v, k in tops))
    return "，".join(parts)


def solve_plans(bundle: ModelBundle) -> Tuple[List[Plan], List[str], str]:
    """返回 (方案列表, 求解过程 warnings, 最终状态字符串)。"""
    cfg = bundle.cfg
    warnings: List[str] = list(bundle.warnings)
    raw: List[Tuple[Dict[Any, int], str]] = []   # (解, 方案名)

    target = max(cfg.solver.min_plans, min(cfg.solver.max_plans, cfg.solver.num_plans))

    # 汉明距离下限：要求后一套方案至少与前一套相差 k 格。
    # 因 H1 保证每格恰好一门课，「变成 0 的原 1 变量个数」就等于「变化的格数」，
    # 所以改成 -k 天然等价于「差异 ≥ k 格」，无需引入辅助变量。
    n_cells = len(bundle.problem.classes) * len(cfg.periods()) * NUM_DAYS
    k = max(2, round(0.03 * n_cells))

    profiles = resolve_profiles(target)
    if not profiles:
        profiles = [(nm, {}) for nm in PLAN_NAMES[:target]]
        warnings.append("权重档位配置异常，已回退为默认命名")

    last_status = "UNKNOWN"
    deadline = time.time() + GLOBAL_BUDGET

    for i, (pname, weights) in enumerate(profiles):
        budget = min(float(cfg.solver.max_time_seconds),
                     max(5.0, (deadline - time.time()) / max(1, len(profiles) - i)))

        # 每套方案换一个优化目标 —— 这是方案真正产生差异的关键
        # （此前所有方案共用同一目标，只靠「禁止完全相同」区分，
        #   求解器返回的是次优孪生解，实测 5 套只差 0.2%）
        apply_objective(bundle, weights)

        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = budget
        solver.parameters.num_search_workers = int(cfg.solver.workers)
        # 固定但各档不同的种子：进一步打散搜索路径，同时保证结果可复现
        solver.parameters.random_seed = 1000 + 37 * i

        status = solver.Solve(bundle.model)
        if status == cp_model.OPTIMAL:
            last_status = "OPTIMAL"
        elif status == cp_model.FEASIBLE:
            last_status = "FEASIBLE"
        elif status == cp_model.INFEASIBLE:
            last_status = "INFEASIBLE"
            warnings.append(
                f"方案「{pname}」无可行解（约束过严，或与已生成方案的差异要求冲突），已跳过")
            continue
        else:  # UNKNOWN / MODEL_INVALID
            last_status = "UNKNOWN"
            warnings.append(f"方案「{pname}」在 {budget:.0f} 秒内未求出可行解，已跳过")
            continue

        sol = {key: solver.Value(v) for key, v in bundle.x.items()}
        raw.append((sol, pname))

        # no-good：下一套方案至少要与本套相差 k 格
        ones = [bundle.x[key] for key, v in sol.items() if v == 1]
        if ones:
            bundle.model.Add(sum(ones) <= len(ones) - min(k, len(ones) - 1))

        if len(raw) >= target:
            break
        if time.time() > deadline:
            warnings.append("求解已接近总时限，停止生成更多方案")
            break

    if not raw:
        return [], warnings, last_status

    # 评分：绝对质量分（所有方案用同一把尺子，不再按相对排名）
    n_classes = len(bundle.problem.classes)
    plans: List[Plan] = []
    for sol, pname in raw:
        m = _calc_metrics(sol, bundle)
        score, sub = absolute_score(m, cfg, n_classes)
        plans.append(Plan(
            index=0,
            name=pname,          # 名字绑定权重档位，保证「名副其实」
            score=score,
            note=_describe(m, sub),
            grid=_extract_grid(sol),
            profile=pname,
        ))

    # 按评分排序（高分在前），只改编号、不改名字
    plans.sort(key=lambda p: -p.score)
    for i, p in enumerate(plans):
        p.index = i + 1

    if len(plans) < cfg.solver.min_plans:
        warnings.append(
            f"约束较严或时限较紧，仅生成 {len(plans)} 套方案（期望 {cfg.solver.min_plans} 套以上）"
        )

    return plans, warnings, last_status
