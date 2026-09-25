# -*- coding: utf-8 -*-
"""CP-SAT 排课压测 第二轮：
A) 加入 连堂(H6)/日课量(H14) 后的求解时间
B) 多解差异化：不同目标权重法
C) 多解差异化：汉明距离约束法（含代价测量）
输出全 ASCII。
"""
import time
from collections import defaultdict
import ortools
from ortools.sat.python import cp_model

T0 = time.time()
BUDGET = 480.0
DAYS, PERIODS = 5, 8
SLOTS = [(d, p) for d in range(DAYS) for p in range(1, PERIODS + 1)]
N_CLASSES = 25
DOUBLE_PAIRS = [(1, 2), (2, 3), (3, 4), (5, 6), (6, 7), (7, 8)]  # 不含(4,5)跨午休

SUBJECTS = [("CHN", 5), ("MAT", 5), ("ENG", 5), ("PHY", 4), ("CHE", 3), ("BIO", 3),
            ("POL", 2), ("HIS", 2), ("GEO", 2), ("PE", 2), ("MUS", 1), ("ART", 1),
            ("ICT", 1), ("MTG", 1)]
TOTAL_HOURS = sum(h for _, h in SUBJECTS)
NO_EARLY = {"PE", "MUS", "ART", "ICT"}
MAIN = {"CHN", "MAT", "ENG"}
ARTS = {"PE", "MUS", "ART", "ICT"}
DOUBLE_SUBJECT = "MAT"          # 数学含 1 次连堂

lessons = []
for c in range(N_CLASSES):
    for subj, h in SUBJECTS:
        if subj in MAIN:
            t = "%s_T%02d" % (subj, c // 2)
        elif subj == "MTG":
            t = "MTG_C%02d" % c
        else:
            t = "%s_T%02d" % (subj, c // 4)
        lessons.append((c, subj, t, h))

print("=" * 74)
print("Round 2 | OR-Tools %s | classes=%d reqs=%d hours/class=%d slack=%d"
      % (ortools.__version__, N_CLASSES, len(lessons), TOTAL_HOURS,
         DAYS * PERIODS - TOTAL_HOURS))
print("=" * 74)

BY_TEACHER = defaultdict(list)
for i, L in enumerate(lessons):
    BY_TEACHER[L[2]].append(i)


def build(profile=None):
    """profile=None 表示不加软目标。"""
    m = cp_model.CpModel()
    x = {}
    for i, (c, subj, t, h) in enumerate(lessons):
        for (d, p) in SLOTS:
            v = m.NewBoolVar("")
            x[i, d, p] = v
            if subj in NO_EARLY and p in (1, 2):
                m.Add(v == 0)
            if subj == "MTG" and not (d == 0 and p == 1):
                m.Add(v == 0)
        m.Add(sum(x[i, d, p] for (d, p) in SLOTS) == h)

    # H1 班级不冲堂
    for c in range(N_CLASSES):
        idx = [i for i, L in enumerate(lessons) if L[0] == c]
        for (d, p) in SLOTS:
            m.Add(sum(x[i, d, p] for i in idx) <= 1)
    # H2 教师不冲堂
    for t, idx in BY_TEACHER.items():
        for (d, p) in SLOTS:
            m.Add(sum(x[i, d, p] for i in idx) <= 1)

    # H15/H6：每天分布 + 连堂
    for i, (c, subj, t, h) in enumerate(lessons):
        if subj == DOUBLE_SUBJECT:
            two, one = [], []
            for d in range(DAYS):
                s = sum(x[i, d, p] for p in range(1, PERIODS + 1))
                m.Add(s <= 2)
                b2 = m.NewBoolVar("")
                m.Add(s == 2).OnlyEnforceIf(b2)
                m.Add(s != 2).OnlyEnforceIf(b2.Not())
                b1 = m.NewBoolVar("")
                m.Add(s == 1).OnlyEnforceIf(b1)
                m.Add(s != 1).OnlyEnforceIf(b1.Not())
                two.append(b2)
                one.append(b1)
                pv = []
                for (a, b) in DOUBLE_PAIRS:
                    v = m.NewBoolVar("")
                    m.Add(v <= x[i, d, a])
                    m.Add(v <= x[i, d, b])
                    m.Add(v >= x[i, d, a] + x[i, d, b] - 1)
                    pv.append(v)
                m.Add(sum(pv) >= b2)          # 2 节那天必须相邻（H6）
            m.Add(sum(two) == 1)              # 恰 1 天连堂
            m.Add(sum(one) == 3)              # 其余 3 天各 1 节
        elif h == 5:                          # 周课时 5 → 每天 1 节
            for d in range(DAYS):
                m.Add(sum(x[i, d, p] for p in range(1, PERIODS + 1)) == 1)
        else:
            for d in range(DAYS):
                m.Add(sum(x[i, d, p] for p in range(1, PERIODS + 1)) <= 1)

    # H14 日课量分级
    for t, idx in BY_TEACHER.items():
        weekly = sum(lessons[i][3] for i in idx)
        if weekly > 12:
            continue
        ncls = len({lessons[i][0] for i in idx})
        lim = 2 if ncls == 1 else 3
        for d in range(DAYS):
            m.Add(sum(x[i, d, p] for i in idx for p in range(1, PERIODS + 1)) <= lim)

    pen = []
    if profile == "main_am":          # 主科不排下午
        pen += [x[i, d, p] for i, L in enumerate(lessons) if L[1] in MAIN
                for d in range(DAYS) for p in range(5, PERIODS + 1)]
    elif profile == "no_last":        # 不排第 8 节（老师早下班）
        pen += [x[i, d, 8] for i in range(len(lessons)) for d in range(DAYS)]
    elif profile == "friday_pm_off":  # 周五下午空出
        pen += [x[i, 4, p] for i in range(len(lessons)) for p in range(5, PERIODS + 1)]
    elif profile == "arts_pm":        # 术科排下午
        pen += [x[i, d, p] for i, L in enumerate(lessons) if L[1] in ARTS
                for d in range(DAYS) for p in range(3, 5)]
    elif profile == "main_not_fri":   # 主科不排周五
        pen += [x[i, 4, p] for i, L in enumerate(lessons) if L[1] in MAIN
                for p in range(1, PERIODS + 1)]
    elif profile == "front_load":     # 前 6 节尽量排满，7-8 节留空
        pen += [x[i, d, p] for i in range(len(lessons))
                for d in range(DAYS) for p in (7, 8)]
    if pen:
        m.Minimize(sum(pen))
    return m, x


def solve(m, limit):
    s = cp_model.CpSolver()
    s.parameters.max_time_in_seconds = limit
    s.parameters.num_search_workers = 8
    t = time.time()
    st = s.Solve(m)
    return s, st, time.time() - t


def hamming_ge(m, x, sols, d):
    """要求新解与 sols 中每个解至少差 d 个变量（纯线性表达式，无辅助变量）。"""
    for sol in sols:
        ones = sum(1 for v in sol.values() if v == 1)
        expr = ones
        for key, v in x.items():
            expr = expr - v if sol[key] == 1 else expr + v
        m.Add(expr >= d)


# ---------------- A) 真实约束下的求解时间 ----------------
print("\n--- A) realistic model (H1,H2,H5,H6,H8,H12,H14,H15) ---")
mA, xA = build(None)
print("[A] vars=%d constraints=%d" % (len(mA.Proto().variables), len(mA.Proto().constraints)))
sA, stA, eA = solve(mA, 120)
print("[A] status=%s wall=%.2fs branches=%d" % (sA.StatusName(stA), eA, sA.NumBranches()))

# ---------------- B) 不同目标权重法 ----------------
print("\n--- B) diversity via different objective weights ---")
PROFILES = ["main_am", "no_last", "friday_pm_off", "arts_pm", "main_not_fri", "front_load"]
plansB, objsB = [], []
for pf in PROFILES:
    if time.time() - T0 > BUDGET:
        print("[B] budget reached")
        break
    m, x = build(pf)
    s, st, e = solve(m, 40)
    if st not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        print("[B] %-14s status=%s" % (pf, s.StatusName(st)))
        continue
    plansB.append({k: s.Value(v) for k, v in x.items()})
    objsB.append(int(s.ObjectiveValue()))
    print("[B] %-14s obj=%-6d wall=%.2fs" % (pf, int(s.ObjectiveValue()), e))

print("\n[B] pairwise differences (vars out of %d, ~2 vars = 1 lesson moved):" % len(plansB[0]))
for a in range(len(plansB)):
    for b in range(a + 1, len(plansB)):
        d = sum(1 for k in plansB[a] if plansB[a][k] != plansB[b][k])
        print("     %-14s vs %-14s : %5d vars  (~%3d lessons)"
              % (PROFILES[a], PROFILES[b], d, d // 2))

# ---------------- C) 汉明距离约束法 ----------------
print("\n--- C) diversity via Hamming-distance constraint (same objective) ---")
for target in (100, 300):
    if time.time() - T0 > BUDGET:
        print("[C] budget reached")
        break
    m, x = build("main_am")
    sols, times = [], []
    for k in range(6):
        if time.time() - T0 > BUDGET:
            break
        s, st, e = solve(m, 45)
        if st not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            print("[C] d=%-4d plan %d UNSAT/UNKNOWN (%s) after %.2fs"
                  % (target, k + 1, s.StatusName(st), e))
            break
        sols.append({key: s.Value(v) for key, v in x.items()})
        times.append(e)
        print("[C] d=%-4d plan %d  obj=%-5d wall=%.2fs"
              % (target, len(sols), int(s.ObjectiveValue()), e))
        hamming_ge(m, x, sols, target)
    if len(sols) >= 2:
        dmin = min(sum(1 for k in sols[a] if sols[a][k] != sols[b][k])
                   for a in range(len(sols)) for b in range(a + 1, len(sols)))
        print("[C] d=%-4d -> %d plans, min pairwise diff=%d vars (~%d lessons), avg %.2fs/plan"
              % (target, len(sols), dmin, dmin // 2, sum(times) / len(times)))

print("\n" + "=" * 74)
print("TOTAL WALL TIME: %.1fs" % (time.time() - T0))
print("=" * 74)
