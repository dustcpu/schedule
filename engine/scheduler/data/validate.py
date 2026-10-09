# -*- coding: utf-8 -*-
"""求解前校验：字段完整性、供需对齐（借鉴 Course-Sorting-Algorithm 的 validate 思路）。

- 致命问题抛 DataError → 上层转 status.json code=2
- 可继续但需注意的返回 warnings
"""
import math
from typing import Dict, List, Tuple

from ..config import NUM_DAYS, SELF_STUDY
from .load import Problem, DataError, NO_TEACHER_SUBJECTS, _walk_cells_of


def validate(p: Problem) -> List[str]:
    warnings: List[str] = []
    cfg = p.config
    grid = cfg.grid_size()

    # 1) 每班周课时总额不得超过可用格数
    # ⚠️ 走班课（is_walk）由走班格承载：既不计入常规课时，对应走班格也要从可用格数
    #    里扣掉。不扣会误判「课时超过可用格数」直接拒绝排课（2026-10-09 实测）。
    for ci in p.classes:
        total = sum(c.weekly for c in p.courses
                    if c.class_id == ci.id and not getattr(c, "is_walk", False))
        walk_cells = _walk_cells_of(p, ci.id)
        avail = grid - walk_cells
        extra = f"（已扣除走班占用 {walk_cells} 格，" if walk_cells else "（"
        if total > avail:
            raise DataError(
                f"班级 {ci.id} 周课时合计 {total} 超过可用格数 {avail}"
                f"{extra}{cfg.schedule.periods_per_day} 节 × {NUM_DAYS} 天），请减少课时"
            )
        if total < avail:
            warnings.append(
                f"班级 {ci.id} 周课时 {total} 少于 {avail}，剩余 {avail - total} 节将作为自习"
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
                f"（「教师」sheet 中任教学科为「{c.subject}」的教师为 0 人），无法排课。"
                f"若「{c.subject}」是不需要教师的课（如信息、心理、书法、劳动等），"
                f"请检查学科名写法是否与「教师」sheet 一致；也可把该学科改成常见写法"
                f"（如「信息」「心理」），或在「教师」sheet 为其补一位教师。"
            )
        cand_of.setdefault(c.subject, set()).update(cands)

    missing = sorted({t for s in cand_of.values() for t in s if t not in p.teachers})
    if missing:
        show = "、".join(missing[:5]) + ("…" if len(missing) > 5 else "")
        warnings.append(f"有 {len(missing)} 位教师ID 未在「教师」sheet 登记：{show}")

    # 2b) 容量可行性：某学科总课时超出候选教师总承载则必然无解
    demand_by_subject: Dict[str, int] = {}
    for c in p.courses:
        # 走班课的教师由教学班表指定（不进 y/z），不计入常规供需核算
        if c.subject in NO_TEACHER_SUBJECTS or getattr(c, "is_walk", False):
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

    # 3c) 连堂时段的教师数下限（结构性；最容易踩、且报错最不友好的一条）
    #
    #     H5 把连堂块的**起始节次**收窄成固定的几个位置（默认只能是第 1 节或第 4 节），
    #     每个班在连堂日恰好排一个连堂块。于是 N 个班的块只能塞进 |P| 个位置里，
    #     由鸽笼原理，至少 ⌈N/|P|⌉ 个班落在同一时段；
    #     而 H3 要求一位教师同一时段最多带 1 个班
    #     ⇒ 该学科至少需要 ⌈N × blocks_per_day / |P|⌉ 位教师。
    #
    #     不满足时模型必然无解。但教师决策打开后搜索空间很大，求解器往往拖满时限
    #     才返回 UNKNOWN，提示语还是"建议减少方案套数"——用户会照着错的方向改。
    #     所以在这里提前秒级判定，直接说清楚缺几位教师。
    consec = cfg.consecutive
    n_pos = len(consec.allow_start_periods) or 1
    block_classes: Dict[str, set] = {}
    for c in p.courses:
        if c.block_len <= 1 or c.subject in NO_TEACHER_SUBJECTS:
            continue
        if consec.day_of(c.subject) is None:
            continue  # 不在连堂规则内，H5 会按普通课时处理
        block_classes.setdefault(c.subject, set()).add(c.class_id)

    for subj, cids in sorted(block_classes.items()):
        n_block = len(cids) * max(1, consec.blocks_per_day)
        need_teachers = math.ceil(n_block / n_pos)
        have = len(cand_of.get(subj, ()))
        if have < need_teachers:
            starts = "、".join(f"第{p}节" for p in consec.allow_start_periods)
            raise DataError(
                f"学科「{subj}」有 {len(cids)} 个班要排连堂，而连堂块只能从 {starts} 开始"
                f"（共 {n_pos} 个位置），因此至少需要 {need_teachers} 位「{subj}」教师"
                f"才能保证同一时段不撞课；当前只有 {have} 位。"
                f"请增加该学科教师，或放宽连堂起始节次。"
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

    # 7) 结构化约束的可行性预检：必然无解的组合在这里秒级拒绝，
    #    不让用户等满求解时限才看到一句"约束互相冲突"（同 §3c 的设计动机）。
    # 7a) 教师连堂禁日：把所有禁日约束合并，某门连堂课的候选被禁光 → 致命
    double_banned: Dict[Tuple[str, str, int], set] = {}
    for sc in p.structured or []:
        if sc.type != "teacher_no_double_day":
            continue
        for c in p.courses:
            if c.subject in NO_TEACHER_SUBJECTS or c.block_len <= 1:
                continue
            dday = consec.day_of(c.subject)
            if dday is None or dday not in sc.days:
                continue
            double_banned.setdefault(
                (c.class_id, c.subject, dday), set()).add(sc.teacher)
    for (cid, subj, dday), banned in sorted(double_banned.items()):
        c = next(cc for cc in p.courses
                 if cc.class_id == cid and cc.subject == subj)
        cands = set(c.teacher_candidates or [])
        if cands and cands <= banned:
            raise DataError(
                f"班级 {cid} 的「{subj}」所有候选教师都要求"
                f"周{'一二三四五'[dday - 1]}不排连堂，而该学科连堂固定在这一天，"
                f"无人可任课。请调整结构化约束（约束转换器对这类冲突有预警），"
                f"或为该学科增加教师"
            )

    # 7e) 教师不可用 vs 连堂学科的"每天恰一节"结构：
    #     连堂学科扣除连堂块后的单节课若被 H5c 强制"非连堂日每天 1 节"
    #     （rest 恰好均分），则该科教师整日禁掉指定日或任一单课日 = 整科不可教。
    #     此时按"有效教师数"重算 3c 的鸽笼下限，不足则提前报错。
    #     （半天/单节的禁排总能把课挪到同日其他节，不构成整科禁教。）
    eff_removed: Dict[str, set] = {}
    for sc in p.structured or []:
        if (sc.type != "teacher_unavailable" or sc.periods
                or sc.teacher not in p.teachers):
            continue
        subj = p.teachers[sc.teacher].subject
        courses_of_s = [c for c in p.courses
                        if c.subject == subj and c.block_len > 1]
        if not courses_of_s:
            continue
        dday = consec.day_of(subj)
        other = [d for d in range(1, NUM_DAYS + 1) if d != dday]
        forced = all(
            (c.weekly - c.block_len * max(1, consec.blocks_per_day)) % len(other) == 0
            for c in courses_of_s)
        if not (forced and (dday in sc.days or any(d in sc.days for d in other))):
            continue
        cids = {c.class_id for c in courses_of_s}
        need = math.ceil(len(cids) * max(1, consec.blocks_per_day)
                         / (len(consec.allow_start_periods) or 1))
        eff_removed.setdefault(subj, set()).add(sc.teacher)
        have = len(cand_of.get(subj, ())) - len(eff_removed[subj])
        if have < need:
            raise DataError(
                f"教师 {p.teacher_name(sc.teacher)}（{sc.teacher}）整周内有整天不可用，"
                f"而「{subj}」的课在非连堂日被强制每天排 1 节，他实际上无法再任教该学科；"
                f"扣除后「{subj}」只剩 {have} 位可用教师，"
                f"但连堂至少需要 {need} 位。请调整不可用要求或增加该学科教师"
            )

    for sc in p.structured or []:
        # 7b) 带班数上限 vs 显式指定的课程数
        if sc.type == "teacher_max_classes":
            locked_n = sum(1 for c in p.courses
                           if c.locked and c.teacher_id == sc.teacher)
            if locked_n > sc.max_classes:
                raise DataError(
                    f"教师 {p.teacher_name(sc.teacher)}（{sc.teacher}）已被显式指定 "
                    f"{locked_n} 个班，超过结构化约束的上限 {sc.max_classes}，必然无解。"
                    f"请放宽上限或取消部分教师指定"
                )
        # 7c) 教师全周不可用 vs 显式指定
        if (sc.type == "teacher_unavailable" and sorted(sc.days) == [1, 2, 3, 4, 5]
                and not sc.periods
                and any(c.locked and c.teacher_id == sc.teacher for c in p.courses)):
            raise DataError(
                f"教师 {p.teacher_name(sc.teacher)}（{sc.teacher}）被设为全周不可用，"
                f"但他有显式指定的课程，必然无解。"
                f"请缩小不可用范围或取消该教师的指定"
            )
        # 7d) 班级禁排格要有自习可填（H1 要求每格恰好一门课）
        if (sc.type == "class_unavailable" and not cfg.solver.self_study_fill
                and not any(c.subject == SELF_STUDY and c.class_id == sc.class_id
                            for c in p.courses)):
            warnings.append(
                f"班级 {sc.class_id} 的禁排格没有自习课可填（且未开启自习自动补齐），"
                f"可能导致无解"
            )

    return warnings
