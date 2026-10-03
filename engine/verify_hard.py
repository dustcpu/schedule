# -*- coding: utf-8 -*-
"""硬约束自检：验证求解结果是否真的满足 H1-H5（跑通 ≠ 正确）。

用法:
    python verify_hard.py <输入目录> [方案序号]                 # 重新求解后自检
    python verify_hard.py <输入目录> --xlsx <result.xlsx>       # 校验真正交付出去的那份文件

注意：默认模式是「重新求解再校验」，校验的是内存里刚算出来的方案，
      不等于 result.xlsx 里那一份（求解带随机性）。要验证交付物请用 --xlsx。

检查项：
  H1 每格唯一且不为空
  H2 每班每科周课时 == 需求
  H3 教师冲突（同一教师同一天同一节不带多个班）
  H4 固定课落在指定 (天, 节)
  H5 连堂：连堂日该科节数 == 块长×块数，且位置连续

--xlsx 模式额外检查交付质量：
  结构（sheet 名 / 输出文件数）· 方案两两差异率 · 评分是否雷同 · warnings 是否重复
"""
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from scheduler.config import NUM_DAYS
from scheduler.data.load import load_problem, NO_TEACHER_SUBJECTS
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

    # H3 教师冲突 —— 必须优先读 plan.assign（求解器决定的任课）。
    # 只读 courses.teacher_id 会在其变为 None（候选 >1 人）后使 `if t:` 恒假，
    # 导致 H3 检查被整体跳过、永远报"全部满足"（假绿）。
    assign = dict(getattr(plan, "assign", None) or {})
    if not assign:
        # 只有存在"真正的决策空间"（候选 >1 人）时才算缺失；
        # 候选恒为 1 人（全部显式指派）时回落到 teacher_id 是合法的。
        if any(c.has_teacher_decision for c in problem.courses):
            # 硬断言：有多候选课程却没有任课解，说明求解/提取链断了，必须显式报错
            errs.append(
                "H25 任课解缺失：存在多候选教师课程，但 plan.assign 为空"
                "（教师分配未纳入求解或解提取失败）")
        assign = {(c.class_id, c.subject): c.teacher_id
                  for c in problem.courses if c.teacher_id}
    slot = defaultdict(list)
    for (cid, d, p), s in plan.grid.items():
        t = assign.get((cid, s))
        if t:
            slot[(t, d, p)].append(cid)
    for (t, d, p), cids in slot.items():
        if len(cids) > 1:
            errs.append(f"H3 教师冲突: {t} 周{d} 第{p}节 同时带 {cids}")

    # H25 教师任职资格：每门课恰好一位教师，且在候选集内、学科匹配
    for c in problem.courses:
        if c.subject in NO_TEACHER_SUBJECTS:
            continue
        if not (c.teacher_candidates or c.teacher_id):
            errs.append(f"H25 无候选教师: {c.class_id} {c.subject}")
            continue
        t = assign.get((c.class_id, c.subject))
        if not t:
            errs.append(f"H25 未指派教师: {c.class_id} {c.subject}")
        elif c.teacher_candidates and t not in c.teacher_candidates:
            errs.append(f"H25 任课不在候选集: {c.class_id} {c.subject} -> {t}")
        elif (problem.teachers.get(t) and problem.teachers[t].subject
              and problem.teachers[t].subject != c.subject):
            errs.append(f"H25 跨学科任课: {t}（任教{problem.teachers[t].subject}）"
                        f" 教 {c.class_id} 的{c.subject}")

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

    # ---- 结构化额外约束（约束转换器 → schema v1）----
    # 复核结果课表确实满足这些约束（跑通 ≠ 正确；与 model.py 的口径一一对应）
    for sc in getattr(problem, "structured", None) or []:
        if sc.type == "teacher_unavailable":
            ps = sc.periods or periods
            for d in sc.days:
                for p in ps:
                    for ci in problem.classes:
                        s = plan.grid.get((ci.id, d, p))
                        t = assign.get((ci.id, s)) if s else None
                        if t == sc.teacher:
                            errs.append(
                                f"结构化约束违反: 教师{sc.teacher} 周{d} 第{p}节"
                                f"被排在 {ci.id}（{s}），但要求该时段不排课")
        elif sc.type == "teacher_no_double_day":
            for c in problem.courses:
                if c.block_len <= 1:
                    continue
                dday = consec.day_of(c.subject)
                if dday is None or dday not in sc.days:
                    continue
                if assign.get((c.class_id, c.subject)) == sc.teacher:
                    errs.append(
                        f"结构化约束违反: 教师{sc.teacher} 教 {c.class_id} 的"
                        f"{c.subject}（连堂固定在周{dday}），但要求该天不排连堂")
        elif sc.type == "class_unavailable":
            ps = sc.periods or periods
            for d in sc.days:
                for p in ps:
                    got = plan.grid.get((sc.class_id, d, p))
                    if got and got != "自习":
                        errs.append(
                            f"结构化约束违反: {sc.class_id} 周{d} 第{p}节"
                            f"排了「{got}」，但要求不排课（只允许自习）")
        elif sc.type == "teacher_max_classes":
            n = sum(1 for v in assign.values() if v == sc.teacher)
            if n > sc.max_classes:
                errs.append(
                    f"结构化约束违反: 教师{sc.teacher}实际带 {n} 个班，"
                    f"超过上限 {sc.max_classes}")

    return errs


def load_result_assigns(path, problem):
    """解析「任课表N」sheet -> {"方案N": {(班级ID, 学科): 教师ID}}。

    单元格可能是「姓名」或「姓名(T012)」两种写法，都要能反查回教师ID。
    """
    import re
    from openpyxl import load_workbook

    name2tid = {}
    for tid, t in problem.teachers.items():
        name2tid.setdefault(t.name, tid)

    wb = load_workbook(path, data_only=True)
    out = {}
    for sn in wb.sheetnames:
        if not sn.startswith("任课表"):
            continue
        ws = wb[sn]
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            continue
        header = [("" if c is None else str(c).strip()) for c in rows[0]]
        subj_cols = [(i, h) for i, h in enumerate(header)
                     if i >= 2 and h and h not in ("班级ID", "班级名称")]
        assign = {}
        for r in rows[1:]:
            if not r or not r[0]:
                continue
            cid = str(r[0]).strip()
            for i, s in subj_cols:
                if i >= len(r) or r[i] is None:
                    continue
                raw = str(r[i]).strip()
                if not raw or raw in ("—", "-"):
                    continue
                m = re.match(r"^(.*?)\s*\(([^()]+)\)$", raw)
                if m and m.group(2) in problem.teachers:
                    assign[(cid, s)] = m.group(2)
                else:
                    tid = name2tid.get(re.sub(r"\s*\(.*\)$", "", raw))
                    if tid:
                        assign[(cid, s)] = tid
        out["方案" + sn.replace("任课表", "", 1)] = assign
    return out


def _resolve_cid(title, classes):
    """课表标题行 → 班级ID。

    export.py 写的是 f"{ci.name or ci.id}　（{选科}）"，选科为空时没有括号后缀，
    标题带后缀，不能直接当班级名用。
    """
    base = title.split("　")[0].strip()
    for c in classes:
        nm = c.name or c.id
        if title == nm or title.startswith(nm) or base == nm:
            return c.id
    return title


def load_result_grids(path, classes, periods):
    """从 result.xlsx 解析每个方案、每个班的课表网格。

    返回 {sheet名: {(班级标题, 天, 节): 学科}}。
    跳过节次 > periods_per_day 的附加行（如体育活动），它们不参与排课。
    """
    from openpyxl import load_workbook

    wb = load_workbook(path, data_only=True)
    out = {}
    for sn in wb.sheetnames:
        if not sn.startswith("方案"):
            continue
        ws = wb[sn]
        grid = {}
        cur = None
        for row in ws.iter_rows(values_only=True):
            cells = [("" if c is None else str(c)) for c in row]
            cells = [c for c in cells if c != ""]
            if not cells:
                continue
            if len(cells) == 1:
                cur = cells[0]
                continue
            if len(cells) >= 6 and cells[0].startswith("第"):
                try:
                    per = int(cells[0].split("节")[0].replace("第", ""))
                except ValueError:
                    continue
                if cur is None or per not in periods:
                    continue
                for i, d in enumerate(range(1, NUM_DAYS + 1), start=1):
                    val = cells[i]
                    # 问题4（2026-10-01）：课表格子自本版起为「学科\n教师」两行，
                    # 校验只认学科 —— 必须取第一行，否则 H1–H5 全部误判。
                    grid[(cur, d, per)] = val.split("\n")[0].strip()
        out[sn] = grid
    return out


def verify_xlsx(in_dir, xlsx_path):
    """校验真正交付出去的那份 result.xlsx（默认模式是重新求解，验不到交付物）。"""
    from scheduler.solver.solve import Plan

    problem = load_problem(in_dir)
    cfg = problem.config
    periods = set(cfg.periods())
    classes = problem.classes

    grids = load_result_grids(xlsx_path, classes, periods)

    status_path = os.path.join(os.path.dirname(xlsx_path), "status.json")
    status = {}
    if os.path.exists(status_path):
        with open(status_path, "r", encoding="utf-8") as f:
            status = json.load(f)

    print(f"交付文件: {xlsx_path}")
    print(f"方案 sheet: {sorted(grids)}")

    errs = []

    # 1) 结构：sheet 名与 plans 对应
    plans_meta = status.get("plans", [])
    if plans_meta:
        expect = {f"方案{p['index']}" for p in plans_meta}
        got = set(grids)
        if expect != got:
            errs.append(f"结构: status.json 声明 {sorted(expect)}，xlsx 实际 {sorted(got)}")

    # 把标题映射回班级ID后逐方案校验 H1-H5
    by_id = {}
    for sheet, g in grids.items():
        reg = {}
        for (title, d, per), s in g.items():
            reg[(_resolve_cid(title, classes), d, per)] = s
        by_id[sheet] = reg

    # 任课解：从「任课表N」sheet 读回（课表网格保持纯学科，教师在这里）
    assigns = load_result_assigns(xlsx_path, problem)

    for sheet in sorted(by_id):
        reg = by_id[sheet]
        meta = next((p for p in plans_meta if f"方案{p['index']}" == sheet), {})
        plan = Plan(index=meta.get("index", 0), name=meta.get("name", sheet),
                    score=meta.get("score", 0), note=meta.get("note", ""),
                    grid=reg, assign=assigns.get(sheet, {}))
        e = check_plan(problem, plan)
        if e:
            errs.append(f"{sheet}（{plan.name}）硬约束违反 {len(e)} 处，"
                        f"例：{e[0]}")
        else:
            print(f"  ✅ {sheet}（{plan.name}，评分 {plan.score}）H1-H5 均满足")

    # 2) 方案两两差异率（协议 §4.1 要求「不同的可行方案」）
    import itertools
    sheets = sorted(by_id)
    if len(sheets) >= 2 and by_id[sheets[0]]:
        keys = list(by_id[sheets[0]].keys())
        rates = []
        for a, b in itertools.combinations(sheets, 2):
            diff = sum(1 for k in keys if by_id[a].get(k) != by_id[b].get(k))
            rates.append(diff / len(keys) * 100)
        print(f"  方案差异率: min {min(rates):.1f}%  max {max(rates):.1f}%  （验收线 ≥5%）")
        if min(rates) < 5:
            errs.append(f"方案过于雷同：最小差异率仅 {min(rates):.1f}%（要求 ≥5%）")

    # 3) 评分是否雷同
    scores = [p.get("score") for p in plans_meta]
    if scores:
        uniq = len(set(scores))
        print(f"  评分: {scores} → {uniq} 个不同值，极差 {max(scores) - min(scores):.1f}")
        if len(scores) >= 2 and uniq < 2:
            errs.append("所有方案评分完全相同，无法体现差异")

    # 4) warnings 是否重复
    ws = status.get("warnings", [])
    if ws and len(ws) != len(set(ws)):
        errs.append(f"warnings 存在重复：{len(ws)} 条中去重后仅 {len(set(ws))} 条")

    if errs:
        print(f"\n❌ 交付文件校验未通过（{len(errs)} 项）：")
        for e in errs:
            print("  -", e)
        return 1
    print("\n✅ 交付文件校验通过（结构 / H1-H5 / 差异率 / 评分 / 告警）")
    return 0


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
        print("      python verify_hard.py <输入目录> --xlsx <result.xlsx>")
        return 1
    in_dir = sys.argv[1]
    args = sys.argv[2:]

    # 校验真正交付出去的那份文件（默认模式是重新求解，验不到交付物）
    if "--xlsx" in args:
        i = args.index("--xlsx")
        if i + 1 >= len(args):
            print("错误: --xlsx 后需要跟 result.xlsx 路径")
            return 1
        return verify_xlsx(in_dir, args[i + 1])

    pick = int(args[0]) if args else 1

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
