# -*- coding: utf-8 -*-
"""求解前校验：字段完整性、供需对齐（借鉴 Course-Sorting-Algorithm 的 validate 思路）。

- 致命问题抛 DataError → 上层转 status.json code=2
- 可继续但需注意的返回 warnings
"""
from typing import List

from ..config import NUM_DAYS, SELF_STUDY
from .load import Problem, DataError


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

    # 2) 教师ID 是否在「教师」sheet 登记
    missing = sorted({
        c.teacher_id for c in p.courses
        if c.teacher_id and c.teacher_id not in p.teachers
    })
    if missing:
        show = "、".join(missing[:5]) + ("…" if len(missing) > 5 else "")
        warnings.append(f"有 {len(missing)} 位教师ID 未在「教师」sheet 登记：{show}")

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
    load_by_teacher = {}
    for c in p.courses:
        if c.teacher_id:
            load_by_teacher[c.teacher_id] = load_by_teacher.get(c.teacher_id, 0) + c.weekly
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
