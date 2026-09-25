# -*- coding: utf-8 -*-
"""CP-SAT 排课可行性压测 —— 25 个行政班的真实高中规模。
输出全 ASCII，避免 Windows 控制台 GBK 编码问题。
"""
import time
from collections import defaultdict
import ortools
from ortools.sat.python import cp_model

T_START = time.time()
DAYS, PERIODS = 5, 8
SLOTS = [(d, p) for d in range(DAYS) for p in range(1, PERIODS + 1)]
N_CLASSES = 25
GLOBAL_BUDGET = 420.0          # 全局时间上限（秒），防止跑飞

SUBJECTS = [
    ("CHN", 5), ("MAT", 5), ("ENG", 5),
    ("PHY", 4),
    ("CHE", 3), ("BIO", 3),
    ("POL", 2), ("HIS", 2), ("GEO", 2),
    ("PE", 2), ("MUS", 1), ("ART", 1), ("ICT", 1),
    ("MTG", 1),
]
TOTAL_HOURS = sum(h for _, h in SUBJECTS)
NO_EARLY = {"PE", "MUS", "ART", "ICT"}
MAIN = {"CHN", "MAT", "ENG"}

lessons = []
for c in range(N_CLASSES):
    for subj, h in SUBJECTS:
        if subj in MAIN:
            t = "%s_T%02d" % (subj, c // 2)       # 主科一位老师带 2 个班
        elif subj == "MTG":
            t = "MTG_C%02d" % c                    # 班主任：一班一人（否则同时段冲突）
        else:
            t = "%s_T%02d" % (subj, c // 4)        # 选考/术科带 4 个班
        lessons.append((c, subj, t, h))

print("=" * 72)
print("OR-Tools %s | Python bench" % ortools.__version__)
print("=" * 72)
print("[scale] classes=%d  lesson-reqs=%d  slots/class=%d"
      % (N_CLASSES, len(lessons), DAYS * PERIODS))
print("[scale] weekly hours/class=%d  slack=%d slots"
      % (TOTAL_HOURS, DAYS * PERIODS - TOTAL_HOURS))


def build(enforce_soft):
    m = cp_model.CpModel()
    x = {}
    for i, (c, subj, t, h) in enumerate(lessons):
        for (d, p) in SLOTS:
            v = m.NewBoolVar("x_%d_%d_%d" % (i, d, p))
            x[i, d, p] = v
            if subj in NO_EARLY and p in (1, 2):
                m.Add(v == 0)
            if subj == "MTG" and not (d == 0 and p == 1):
                m.Add(v == 0)
        m.Add(sum(x[i, d, p] for (d, p) in SLOTS) == h)

    for c in range(N_CLASSES):
        idx = [i for i, L in enumerate(lessons) if L[0] == c]
        for (d, p) in SLOTS:
            m.Add(sum(x[i, d, p] for i in idx) <= 1)

    by_t = defaultdict(list)
    for i, L in enumerate(lessons):
        by_t[L[2]].append(i)
    for t, idx in by_t.items():
        for (d, p) in SLOTS:
            m.Add(sum(x[i, d, p] for i in idx) <= 1)

    for i, (c, subj, t, h) in enumerate(lessons):
        if subj in MAIN:
            for d in range(DAYS):
                m.Add(sum(x[i, d, p] for p in range(1, PERIODS + 1)) <= 1)

    pen = []
    if enforce_soft:
        for i, (c, subj, t, h) in enumerate(lessons):
            if subj in MAIN:
                for d in range(DAYS):
                    for p in range(5, PERIODS + 1):
                        pen.append(x[i, d, p])
        m.Minimize(sum(pen))
    return m, x, len(pen)


def new_solver(limit):
    s = cp_model.CpSolver()
    s.parameters.max_time_in_seconds = limit
    s.parameters.num_search_workers = 8
    return s


# ---------------- Phase 1: 纯硬约束，找第一个可行解 ----------------
print("\n--- Phase 1: feasibility only (hard constraints) ---")
m1, x1, _ = build(False)
s1 = new_solver(60)
t0 = time.time()
st1 = s1.Solve(m1)
e1 = time.time() - t0
print("[P1] vars=%d constraints=%d" % (len(m1.Proto().variables), len(m1.Proto().constraints)))
print("[P1] status=%s  wall=%.2fs  conflicts=%d  branches=%d"
      % (s1.StatusName(st1), e1, s1.NumConflicts(), s1.NumBranches()))

# ---------------- Phase 2: 加软约束目标 ----------------
print("\n--- Phase 2: with soft objective (main subjects in morning) ---")
m2, x2, npen = build(True)
s2 = new_solver(60)
t0 = time.time()
st2 = s2.Solve(m2)
e2 = time.time() - t0
obj = int(s2.ObjectiveValue()) if st2 in (cp_model.OPTIMAL, cp_model.FEASIBLE) else None
print("[P2] vars=%d soft_terms=%d" % (len(m2.Proto().variables), npen))
print("[P2] status=%s  wall=%.2fs  objective=%s  best_bound=%.1f"
      % (s2.StatusName(st2), e2, obj, s2.BestObjectiveBound()))

# ---------------- Phase 3: 多解枚举（no-good cut）----------------
print("\n--- Phase 3: multi-solution enumeration (3-8 plans) ---")
m3, x3, _ = build(True)
s3 = new_solver(25)
plans = []
t_all = time.time()
for k in range(8):
    if time.time() - T_START > GLOBAL_BUDGET:
        print("[P3] global budget reached, stopping")
        break
    st = s3.Solve(m3)
    if st not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        print("[P3] stop at plan %d (status=%s)" % (k + 1, s3.StatusName(st)))
        break
    assign = {key: s3.Value(v) for key, v in x3.items()}
    plans.append(assign)
    print("[P3] plan %d  obj=%d  cum=%.2fs"
          % (len(plans), int(s3.ObjectiveValue()), time.time() - t_all))
    diff = []
    for key, v in x3.items():
        b = m3.NewBoolVar("")
        m3.Add(v != s3.Value(v)).OnlyEnforceIf(b)
        m3.Add(v == s3.Value(v)).OnlyEnforceIf(b.Not())
        diff.append(b)
    m3.Add(sum(diff) >= 1)

# ---------------- Phase 4: 方案差异度（汉明距离）----------------
print("\n--- Phase 4: solution diversity (pairwise Hamming distance) ---")
if len(plans) >= 2:
    print("[P4] plans=%d  total_vars=%d" % (len(plans), len(plans[0])))
    mind, maxd = None, None
    for a in range(len(plans)):
        for b in range(a + 1, len(plans)):
            d = sum(1 for k in plans[a] if plans[a][k] != plans[b][k])
            pct = 100.0 * d / len(plans[0])
            mind = d if mind is None else min(mind, d)
            maxd = d if maxd is None else max(maxd, d)
            print("[P4] plan%d vs plan%d : differ in %d vars (%.2f%%)" % (a + 1, b + 1, d, pct))
    print("[P4] min_diff=%d  max_diff=%d" % (mind, maxd))
else:
    print("[P4] fewer than 2 plans, cannot compare")

print("\n" + "=" * 72)
print("TOTAL WALL TIME: %.1fs" % (time.time() - T_START))
print("=" * 72)
