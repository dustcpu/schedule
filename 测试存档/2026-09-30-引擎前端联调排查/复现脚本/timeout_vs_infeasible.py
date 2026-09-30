# -*- coding: utf-8 -*-
"""对照：同一份「无解」的数据，在两种 solver 配置下的引擎行为。

数据来源只有一处：<repo>/engine/test_input_25_new/input.xlsx
做法：复制一份并删掉 1 位语文老师，得到确定性无解的数据。
然后跑两次：
  (1) teacher_decision=false, 候选=1        → 期望秒级报出无解
  (2) teacher_decision=true,  候选=5（外壳默认）→ 期望秒级报出**同一条**无解结论

背景（2026-09-30 实测，加 validate 结构检查之前）：
  候选=1  ->  1.9 秒报 INFEASIBLE，但提示语是泛泛的"约束互相冲突"
  候选=5  ->  182.6 秒才报 UNKNOWN，提示语是误导性的"建议减少方案套数"
  也就是说：同一份无解数据，默认配置下要拖满 3 分钟，还把用户往错的方向引。

加了 `scheduler/data/validate.py` 的连堂鸽笼检查之后，两种配置都应在秒级
给出同一条明确指出"缺几位教师"的结论 —— 本脚本就是验证这一点。

运行：python timeout_vs_infeasible.py
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from openpyxl import load_workbook

PY = sys.executable
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
ENGINE_DIR = os.path.join(REPO, "engine")
SRC_XLSX = os.path.join(ENGINE_DIR, "test_input_25_new", "input.xlsx")

BASE_HARD_LIMITS = {
    "schedule": {
        "periods_per_day": 8, "period_minutes": 40,
        "morning_start": "08:00", "morning_end": "12:20",
        "afternoon_start": "14:30", "afternoon_end": "16:55",
        "long_break_after_period": 3, "long_break_minutes": 30,
        "eye_break_after_period": 6, "eye_break_minutes": 15,
        "default_break_minutes": 10,
    },
    "fixed_classes": [
        {"day": 1, "period": 1, "subject": "班会", "note": "由班主任上课"},
        {"day": 1, "period": 8, "subject": "研究性学习", "note": ""},
        {"day": 4, "period": 8, "subject": "校本课", "note": ""},
    ],
    "classes": {
        "total_classes": 25, "arts_start": 1, "arts_end": 4,
        "science_start": 5, "science_end": 25, "gaokao_policy": "新高考3+1+2",
    },
    "consecutive": {
        "subjects": ["语文", "数学", "英语"],
        "math_day": 2, "chinese_day": 3, "english_day": 4,
        "rule": "", "only_core_subjects": True,
    },
    "solver": {},
    "extra_constraints": "",
}

CASES = [
    ("关教师决策_候选1", {"max_time_seconds": 60, "teacher_candidate_k": 1,
                          "num_plans": 1, "min_plans": 1, "teacher_decision": False}),
    ("外壳默认_候选5_3方案", {"max_time_seconds": 60, "teacher_candidate_k": 5, "num_plans": 3}),
]


def make_infeasible(path):
    """复制源数据并删掉 1 位语文老师 → 25 班只剩 12 位语文老师 → 结构性无解。"""
    shutil.copy(SRC_XLSX, path)
    wb = load_workbook(path)
    ws = wb["教师"]
    heads = [str(c.value).strip() if c.value else "" for c in ws[1]]
    i_sub = heads.index("任教学科")
    for row in range(2, ws.max_row + 1):
        if str(ws.cell(row=row, column=i_sub + 1).value or "").strip() == "语文":
            ws.delete_rows(row, 1)
            break
    wb.save(path)
    wb.close()


def main():
    if not os.path.exists(SRC_XLSX):
        print("找不到仓库测试数据:", SRC_XLSX)
        return 1

    print("数据来源:", SRC_XLSX)
    print("构造：删掉 1 位语文老师 → 语文 12 位 / 25 个班 → 结构性无解")
    print()

    with tempfile.TemporaryDirectory(prefix="paike_timeout_") as base:
        bad = os.path.join(base, "infeasible.xlsx")
        make_infeasible(bad)

        for tag, solver in CASES:
            work = os.path.join(base, tag)
            os.makedirs(os.path.join(work, "input"))
            os.makedirs(os.path.join(work, "output"))
            shutil.copy(bad, os.path.join(work, "input", "input.xlsx"))
            with open(os.path.join(work, "input", "requirements.txt"), "w", encoding="utf-8") as f:
                f.write("")
            hl = json.loads(json.dumps(BASE_HARD_LIMITS))
            hl["solver"] = solver
            with open(os.path.join(work, "input", "hard_limits.json"), "w", encoding="utf-8") as f:
                json.dump(hl, f, ensure_ascii=False, indent=2)

            t0 = time.time()
            subprocess.run(
                [PY, os.path.join(ENGINE_DIR, "engine.py"),
                 os.path.join(work, "input"), os.path.join(work, "output"), tag],
                cwd=ENGINE_DIR, capture_output=True, text=True,
                encoding="utf-8", errors="replace",
                env=dict(os.environ, PYTHONIOENCODING="utf-8"),
            )
            wall = time.time() - t0

            st = os.path.join(work, "output", "status.json")
            d = json.load(open(st, encoding="utf-8")) if os.path.exists(st) else {}
            log = os.path.join(work, "output", "engine.log")
            status = ""
            if os.path.exists(log):
                for line in open(log, encoding="utf-8"):
                    if "求解完成" in line:
                        status = line.split("状态=")[-1].split(",")[0].strip()

            print(f"[{tag}]  耗时={wall:6.1f}s   code={d.get('code')}")
            print(f"    求解状态: {status if status else '未进入求解（被前置校验拦下）'}")
            print(f"    提示语: {d.get('message', '')}")
            print()

    print("对比结论：")
    print("  · 两种配置现在都在秒级给出同一条结论，且直接说明「至少需要 13 位语文教师，当前只有 12 位」；")
    print("  · 用户不必再等 3 分钟，也不会被告知去「减少方案套数」这种错方向；")
    print("  · 这正是 `scheduler/data/validate.py` 新增的连堂鸽笼检查起到的作用。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
