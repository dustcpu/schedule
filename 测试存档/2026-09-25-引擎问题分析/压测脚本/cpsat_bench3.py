# -*- coding: utf-8 -*-
"""Part C 单独复现：汉明距离约束法的代价与稳定性。带完整异常输出。"""
import sys, time, traceback
from collections import defaultdict
import ortools
from ortools.sat.python import cp_model

T0 = time.time()
DAYS, PERIODS = 5, 8
SLOTS = [(d, p) for d in range(DAYS) for p in range(1, PERIODS + 1)]
N_CLASSES = 25
DOUBLE_PAIRS = [(1, 2), (2, 3), (3, 4), (5, 6), (6, 7), (7, 8)]
SUBJECTS = [("CHN", 5), ("MAT", 5), ("ENG", 5), ("PHY", 4), ("CHE", 3), ("BIO", 3),
            ("POL", 2), ("HIS", 2), ("GEO", 2), ("PE", 2), ("MUS", 1), ("ART", 1),
            ("ICT", 1), ("MTG", 1)]
NO_EARLY = {"PE", "MUS", "ART", "ICT"}
MAIN = {"CHN", "MAT", "ENG"}
DOUBLE_SUBJECT = "MAT"

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
BY_T = defaultdict(list)
for i, L in enumerate(lessons):
    BY_T[L[2]].append(i)

print("OR-Tools %s | building model..." % ortools.__version__)
sys.stdout.flush()


def build():
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
    for c in range(N_CLASSES):
        idx = [i for i, L in enumerate(lessons) if L[0] == c]
        for (d, p) in SLOTS:
            m.Add(sum(x[i, d, p] for i in idx) <= 1)
    for t, idx in BY_T.items():
        for (d, p) in SLOTS:
            m.Add(sum(x[i, d, p] for i in idx) <= 1)
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
                m.Add(sum(pv) >= b2)
            m.Add(sum(two) == 1)
            m.Add(sum(one) == 3)
        elif h == 5:
            for d in range(DAYS):
                m.Add(sum(x[i, d, p] for p in range(1, PERIODS + 1)) == 1)
        else:
            for d in range(DAYS):
                m.Add(sum(x[i, d, p] for p in range(1, PERIODS + 1)) <= 1)
    for t, idx in BY_T.items():
        weekly = sum(lessons[i][3] for i in idx)
        if weekly > 12:
            continue
        ncls = len({lessons[i][0] for i in idx})
        lim = 2 if ncls == 1 else 3
        for d in range(DAYS):
            m.Add(sum(x[i, d, p] for i in idx for p in range(1, PERIODS + 1)) <= lim)
    pen = [x[i, d, p] for i, L in enumerate(lessons) if L[1] in MAIN
           for d in range(DAYS) for p in range(5, PERIODS + 1)]
    m.Minimize(sum(pen))
    return m, x


def solve(m, limit):
    s = cp_model.CpSolver()
    s.parameters.max_time_in_seconds = limit
    s.parameters.num_search_workers = 8
    t = time.time()
    st = s.Solve(m)
    return s, st, time.time() - t


def add_hamming(m, x, sols, d):
    """用线性表达式累加差异，避免深层嵌套。"""
    for sol in sols:
        terms = []
        for key, v in x.items():
            terms.append(1 - v if sol[key] == 1 else v)
        m.Add(sum(terms) >= d)


try:
    for target in (100, 300, 600):
        print("\n=== Hamming d=%d ===" % target)
        sys.stdout.flush()
        m, x = build()
        sols, times = [], []
        for k in range(6):
            s, st, e = solve(m, 45)
            if st not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
                print("  plan %d -> %s (%.2fs)" % (k + 1, s.StatusName(st), e))
                break
            sols.append({key: s.Value(v) for key, v in x.items()})
            times.append(e)
            print("  plan %d  obj=%-5d wall=%.2fs" % (len(sols), int(s.ObjectiveValue()), e))
            sys.stdout.flush()
            t_add = time.time()
            add_hamming(m, x, sols, target)
            print("     (add constraints: %.2fs)" % (time.time() - t_add))
            sys.stdout.flush()
        if len(sols) >= 2:
            dmin = min(sum(1 for k in sols[a] if sols[a][k] != sols[b][k])
                       for a in range(len(sols)) for b in range(a + 1, len(sols)))
            print("  => %d plans, min pairwise diff=%d vars (~%d lessons), avg %.2fs/plan"
                  % (len(sols), dmin, dmin // 2, sum(times) / len(times)))
except Exception:
    traceback.print_exc()
    sys.exit(1)

print("\nTOTAL %.1fs" % (time.time() - T0))
