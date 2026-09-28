# -*- coding: utf-8 -*-
"""求解前校验：字段完整性、供需对齐（借鉴 Course-Sorting-Algorithm 的 validate 思路）。

- 致命问题抛 DataError → 上层转 status.json code=2
- 可继续但需注意的返回 warnings
"""
from typing import Dict, List

from ..config import NUM_DAYS, SELF_STUDY
from .load import Problem, DataError, NO_TEACHER_SUBJECTS


def validate(p: Problem) -> List[str]:
    warnings: List[str] = []
    cfg = p.config
    grid = cfg.grid_size()

    # 1) 每班周课时总额不得超过可用格数
    for ci in p.classes:
        total = sum(c.weekly for c in p.courses if c.class_id == ci.id)
        if total > grid:
            raise DataError(
                f"班级 {ci.id} 周课时合计 {total} 超过可用格数 {grid}"
                f"（{cfg.schedule.periods_per_day} 节 × {NUM_DAYS} 天），请减少课时"
            )
        if total < grid:
            warnings.append(
                f"班级 {ci.id} 周课时 {total} 少于 {grid}，剩余 {grid - total} 节将作为自习"
            )

    # 2) 候选教师是否登记 + 空候选是否致命
    # 改造后 teacher_id 可能为空（候选 >1 人），必须遍历候选集；
    # 且空候选会导致建模时不建 y 变量 → 教师冲突约束静默失效，属致命。
    cand_of: Dict[str, set] = {}
    for c in p.courses:
        if c.subject in NO_TEACHER_SUBJECTS:
            continue
        cands = list(c.teacher_candidates or ([] if not c.teacher_id else [c.teacher_id]))
        if not cands:
            raise DataError(
                f"班级 {c.class_id} 的「{c.subject}」没有可用任课教师"
                f"（「教师」sheet 中任教学科为「{c.subject}」的教师为 0 人），无法排课"
            )
        cand_of.setdefault(c.subject, set()).update(cands)

    missing = sorted({t for s in cand_of.values() for t in s if t not in p.teachers})
    if missing:
        show = "、".join(missing[:5]) + ("…" if len(missing) > 5 else "")
        warnings.append(f"有 {len(missing)} 位教师ID 未在「教师」sheet 登记：{show}")

    # 2b) 容量可行性：某学科总课时超出候选教师总承载则必然无解
    demand_by_subject: Dict[str, int] = {}
    for c in p.courses:
        if c.subject in NO_TEACHER_SUBJECTS:
            continue
        demand_by_subject[c.subject] = demand_by_subject.get(c.subject, 0) + c.weekly
    for subj, need in demand_by_subject.items():
        m = len(cand_of.get(subj, ()))
        if m == 0:
            raise DataError(f"学科「{subj}」没有任何可用任课教师，无法排课")
        if need > m * grid:
            raise DataError(
                f"学科「{subj}」周课时合计 {need} 节，但候选教师只有 {m} 位，"
                f"最多承担 {m * grid} 节（每人每周 {grid} 格）。"
                f"请增加该学科教师、减少课时，或放宽候选教师数量"
            )
        if need / m > grid * 0.9:
            warnings.append(
                f"学科「{subj}」人均周课时 {need / m:.1f} 节，"
                f"已接近上限 {grid} 节，排课会比较紧张"
            )
        idle = [t for t in p.teachers.values()
                if t.subject == subj and t.id not in cand_of[subj]]
        if idle:
            show = "、".join(p.teacher_name(t.id) for t in idle[:3])
            warnings.append(
                f"学科「{subj}」有 {len(idle)} 位教师未进入任何课程的候选集（如 {show}），"
                f"本周将无法排到课"
            )

    # 3) 连堂学科课时是否够排
    for c in p.courses:
        if c.block_len > 0:
            need = c.block_len * max(1, cfg.consecutive.blocks_per_day)
            if c.weekly < need:
                raise DataError(
                    f"班级 {c.class_id} 的「{c.subject}」周课时 {c.weekly} "
                    f"不足以排 {need} 节连堂"
                )

    # 4) 固定课学科是否出现在课程表
    declared = {c.subject for c in p.courses}
    for fc in cfg.fixed_classes:
        if fc.subject not in declared:
            warnings.append(f"固定课「{fc.subject}」未出现在课程表中，已忽略")

    # 5) 同一教师带班过多（提示，非致命）
    # 用 hint_assign（贪心预期分配）而非 teacher_id：改造后 teacher_id 可能为空
    load_by_teacher = {}
    for c in p.courses:
        t = p.hint_assign.get((c.class_id, c.subject)) or c.teacher_id
        if t:
            load_by_teacher[t] = load_by_teacher.get(t, 0) + c.weekly
    over = {t: n for t, n in load_by_teacher.items() if n > grid}
    if over:
        for t, n in list(over.items())[:3]:
            warnings.append(
                f"教师 {p.teacher_name(t)} 周课时 {n} 超过单班格数 {grid}，"
                f"可能排不下（其带多个班时受同时段冲突限制）"
            )

    # 6) 自习
    if not any(c.subject == SELF_STUDY for c in p.courses):
        warnings.append("课程表未指定「自习」课时，已由内核用剩余格子自动补齐")

    return warnings
