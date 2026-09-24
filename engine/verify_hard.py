# -*- coding: utf-8 -*-
"""硬约束自检：验证求解结果是否真的满足 H1-H5（跑通 ≠ 正确）。

用法: python verify_hard.py <输入目录> [检查第几套方案，默认第1套]

检查项：
  H1 每格唯一且不为空
  H2 每班每科周课时 == 需求
  H3 教师冲突（同一教师同一天同一节不带多个班）
  H4 固定课落在指定 (天, 节)
  H5 连堂：连堂日该科节数 == 块长×块数，且位置连续
"""
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from scheduler.config import NUM_DAYS
from scheduler.data.load import load_problem
from scheduler.data.validate import validate
from scheduler.solver.model import build_model
from scheduler.solver.solve import solve_plans


def check_plan(problem, plan) -> list:
    cfg = problem.config
    periods = cfg.periods()
    days = list(range(1, NUM_DAYS + 1))
    errs = []

    # H1 每格唯一且不为空
    for ci in problem.classes:
        for d in days:
            for p in periods:
                if not plan.grid.get((ci.id, d, p)):
                    errs.append(f"H1 空格: {ci.id} 周{d} 第{p}节")

    # H2 周课时
    for c in problem.courses:
        cnt = sum(1 for (cid, _d, _p), s in plan.grid.items()
                  if cid == c.class_id and s == c.subject)
        if cnt != c.weekly:
            errs.append(f"H2 课时不符: {c.class_id} {c.subject} 期望{c.weekly} 实际{cnt}")

    # H3 教师冲突
    teacher_of = {(c.class_id, c.subject): c.teacher_id for c in problem.courses}
    slot = defaultdict(list)
    for (cid, d, p), s in plan.grid.items():
        t = teacher_of.get((cid, s))
        if t:
            slot[(t, d, p)].append(cid)
    for (t, d, p), cids in slot.items():
        if len(cids) > 1:
            errs.append(f"H3 教师冲突: {t} 周{d} 第{p}节 同时带 {cids}")

    # H4 固定课
    for fc in cfg.fixed_classes:
        if not (1 <= fc.day <= NUM_DAYS) or fc.period not in periods:
            continue
        for ci in problem.classes:
            declared = any(c.class_id == ci.id and c.subject == fc.subject
                           for c in problem.courses)
            if not declared:
                continue
            got = plan.grid.get((ci.id, fc.day, fc.period))
            if got != fc.subject:
                errs.append(f"H4 固定课未满足: {ci.id} 周{fc.day} 第{fc.period}节 "
                            f"期望「{fc.subject}」实际「{got}」")

    # H5 连堂
    consec = cfg.consecutive
    for c in problem.courses:
        if c.block_len <= 0:
            continue
        day = consec.day_of(c.subject)
        if not day:
            continue
        idx = [p for p in periods if plan.grid.get((c.class_id, day, p)) == c.subject]
        expect = c.block_len * max(1, consec.blocks_per_day)
        if len(idx) != expect:
            errs.append(f"H5 连堂节数: {c.class_id} {c.subject} 周{day} "
                        f"期望{expect} 实际{len(idx)}")
        elif idx != list(range(idx[0], idx[0] + len(idx))):
            errs.append(f"H5 连堂不连续: {c.class_id} {c.subject} 周{day} 位置{idx}")

    return errs


def print_grid(problem, plan, class_id):
    cfg = problem.config
    periods = cfg.periods()
    days = list(range(1, NUM_DAYS + 1))
    labels = cfg.period_labels()
    print(f"\n【{class_id} 课表】方案{plan.index}（{plan.name}，评分 {plan.score}）")
    print("        " + "".join(f"{('周' + str(d)):>8}" for d in days))
    for p in periods:
        row = "".join(f"{plan.grid.get((class_id, d, p), ''):>8}" for d in days)
        print(f"{labels[p - 1]:<16}{row}")


def main():
    if len(sys.argv) < 2:
        print("用法: python verify_hard.py <输入目录> [方案序号]")
        return 1
    in_dir = sys.argv[1]
    pick = int(sys.argv[2]) if len(sys.argv) > 2 else 1

    problem = load_problem(in_dir)
    warnings = validate(problem)
    bundle = build_model(problem)
    plans, solve_warnings, status = solve_plans(bundle)

    print(f"求解状态: {status}   方案数: {len(plans)}")
    if warnings:
        print("数据层 warnings:")
        for w in warnings:
            print("  -", w)

    if not plans:
        print("没有生成任何方案，无法校验")
        return 2

    target = next((p for p in plans if p.index == pick), plans[0])
    errs = check_plan(problem, target)

    print(f"\n=== 校验方案 {target.index}（{target.name}）===")
    if errs:
        print(f"❌ 发现 {len(errs)} 处硬约束违反：")
        for e in errs[:20]:
            print("  -", e)
        return 1
    print("✅ 全部硬约束 H1-H5 均满足")

    print_grid(problem, target, problem.classes[0].id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
