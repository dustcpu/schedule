# -*- coding: utf-8 -*-
"""进程内小模型测试：验证结构化约束在 CP-SAT 里真的生效（秒级）。

25 班数据上「绑定的连堂禁日」必然无解（13 教师 × 2 位置 = 26 ≈ 25 班，
鸽笼极限），所以这里用 2 班小模型直接验证建模语义。
"""
import os
import sys

ENGINE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))), "engine")
sys.path.insert(0, ENGINE)

from scheduler.config import Config                      # noqa: E402
from scheduler.data.load import (Problem, ClassInfo, TeacherInfo,  # noqa: E402
                                 CourseReq, StructuredConstraint,
                                 apply_structured_double_bans)
from scheduler.data.validate import validate             # noqa: E402
from scheduler.solver.model import build_model           # noqa: E402
from scheduler.solver.solve import solve_plans           # noqa: E402

cfg = Config()
cfg.solver.max_time_seconds = 20
cfg.solver.num_plans = 1
cfg.solver.min_plans = 1

teachers = {}
SUBJECTS = [("语文", 6, 2), ("数学", 6, 2), ("英语", 6, 2), ("物理", 4, 2),
            ("历史", 4, 2), ("化学", 4, 2), ("生物", 4, 2), ("政治", 2, 2)]
courses = []
for ci, cid in enumerate(["C01", "C02"]):
    pass
classes = [ClassInfo(id="C01", name="高一(1)班"), ClassInfo(id="C02", name="高一(2)班")]
tid_n = 100
for subj, weekly, n_t in SUBJECTS:
    for i in range(n_t):
        tid = f"T{tid_n}"
        tid_n += 1
        teachers[tid] = TeacherInfo(id=tid, name=f"{subj}老师{tid[1:]}", subject=subj)
        # 每位教师进两个班的候选（容量富余，便于测试禁排/上限）
for cid in ("C01", "C02"):
    for subj, weekly, _n in SUBJECTS:
        if subj == "体育":
            courses.append(CourseReq(class_id=cid, subject=subj, teacher_id=None,
                                     weekly=2, block_len=0))
            continue
        pool = [t for t, info in teachers.items() if info.subject == subj]
        courses.append(CourseReq(class_id=cid, subject=subj, teacher_id=None,
                                 weekly=weekly, block_len=2 if subj in ("语文", "数学", "英语") else 0,
                                 teacher_candidates=pool))
rest = 40 - sum(c.weekly for c in courses if c.class_id == "C01")
courses.append(CourseReq(class_id="C01", subject="自习", teacher_id=None, weekly=rest))
courses.append(CourseReq(class_id="C02", subject="自习", teacher_id=None,
                         weekly=40 - sum(c.weekly for c in courses if c.class_id == "C02")))

structured = [
    # 绑定的连堂禁日：数学连堂固定周二，T103 禁周二连堂 → 他不能教任何数学
    StructuredConstraint(type="teacher_no_double_day", teacher="T103", days=[2]),
    # 教师周一不可用（英语老师 T105）
    StructuredConstraint(type="teacher_unavailable", teacher="T105", days=[1]),
    # 班级 C01 周五第8节不排课（只排自习）
    StructuredConstraint(type="class_unavailable", class_id="C01", days=[5],
                         periods=[8]),
    # 语文老师 T101 最多带 1 个班
    StructuredConstraint(type="teacher_max_classes", teacher="T101", max_classes=1),
]

hint = {}
apply_structured_double_bans(courses, structured, teachers, cfg, [], hint)
p = Problem(classes=classes, teachers=teachers, courses=courses, config=cfg,
            structured=structured, hint_assign=hint)

errs0 = validate(p)
print("validate warnings:", len(errs0))
for w in errs0:
    print("  ", w)

bundle = build_model(p)
plans, solve_warnings, status = solve_plans(bundle)
print("求解状态:", status, "方案数:", len(plans))
assert plans, "没有解！"

plan = plans[0]
assign = plan.assign

fails = []
# 1) T201 不教任何数学
math_classes = {cid for (cid, s), t in assign.items()
                if s == "数学" and t == "T103"}
if math_classes:
    fails.append(f"T201 仍教数学：{math_classes}")
# 2) T301 周一没课
for (cid, d, per), s in plan.grid.items():
    t = assign.get((cid, s))
    if t == "T105" and d == 1:
        fails.append(f"T301 周一第{per}节有课（{cid} {s}）")
# 3) C01 周五第8节是自习
got = plan.grid.get(("C01", 5, 8))
if got != "自习":
    fails.append(f"C01 周五第8节 = {got}，期望自习")
# 4) T101 最多带 1 个班
n101 = sum(1 for v in assign.values() if v == "T101")
if n101 > 1:
    fails.append(f"T101 带了 {n101} 个班")

# 5) verify_hard 的 check_plan 对该方案应零违反
sys.path.insert(0, ENGINE)
from verify_hard import check_plan  # noqa: E402
errs = check_plan(p, plan)
if errs:
    fails.append(f"check_plan 报告违反：{errs[:5]}")

# 生效回显必须在 warnings 里
echo = [w for w in (p.warnings + solve_warnings) if "结构化约束已生效" in w]
print("生效回显：")
for w in echo:
    print("  ", w)
if len(echo) < 3:
    fails.append(f"生效回显只有 {len(echo)} 条（期望 3，连堂禁日走候选收缩）")

if fails:
    print("\n未通过：")
    for f in fails:
        print("  ✗", f)
    sys.exit(1)
print("\n✅ 小模型 4 类结构化约束全部生效，check_plan 零违反")
