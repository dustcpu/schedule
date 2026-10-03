# -*- coding: utf-8 -*-
"""结构化约束（引擎侧）冒烟测试：load → validate 链路，秒级，不求解。

用法：python smoke_structured.py <仓库内 schedule-git 根>
"""
import json
import os
import shutil
import subprocess
import sys

REPO = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
ENGINE = os.path.join(REPO, "engine")
SRC = os.path.join(ENGINE, "test_input_25_new")
TMP = os.path.join(os.environ.get("TEMP", "/tmp"), "structured_smoke")
sys.path.insert(0, ENGINE)

from scheduler.data.load import DataError  # noqa: E402

FENCE = """1.张卫东教C05数学

```json
{
  "version": 1,
  "constraints": [
    {"type": "teacher_unavailable", "teacher": "T001", "days": [2], "periods": [6, 7, 8]},
    {"type": "teacher_no_double_day", "teacher": "T003", "days": [2]},
    {"type": "class_unavailable", "class": "C01", "day": 5, "periods": [7, 8]},
    {"type": "teacher_max_classes", "teacher": "T001", "max": 2},
    {"type": "assign_teacher", "teacher": "T002", "class": "C01", "subject": "语文"},
    {"type": "time_travel", "teacher": "T001"},
    {"type": "teacher_unavailable", "teacher": "T999", "days": [9]}
  ]
}
```
"""

FILE_JSON = {
    "version": 1,
    "constraints": [
        {"type": "assign_teacher", "teacher": "T004", "class": "C01", "subject": "语文"},
        {"type": "teacher_unavailable", "teacher": "T002", "days": [5]},
    ],
}


def prepare(with_file=True, fence=FENCE):
    if os.path.exists(TMP):
        shutil.rmtree(TMP)
    shutil.copytree(SRC, TMP)
    with open(os.path.join(TMP, "requirements.txt"), "w", encoding="utf-8") as f:
        f.write(fence)
    if with_file:
        with open(os.path.join(TMP, "structured_requirements.json"), "w",
                  encoding="utf-8") as f:
            json.dump(FILE_JSON, f, ensure_ascii=False, indent=2)


def load_and_validate():
    from scheduler.data.load import load_problem, DataError
    from scheduler.data.validate import validate
    p = load_problem(TMP)
    print("--- load warnings ---")
    for w in p.warnings:
        if "结构化" in w or "围栏" in w or "JSON" in w:
            print("  ", w)
    print("--- structured ---")
    for c in p.structured:
        print("  ", c.type, c.teacher, c.class_id, c.days, c.periods, c.max_classes)
    print("--- validate ---")
    try:
        extra = validate(p)
        for w in extra:
            print("  warn:", w)
        print("  validate OK")
    except DataError as e:
        print("  DataError（符合预期）:", e)
    # 教师指定应用结果
    c01 = [c for c in p.courses if c.class_id == "C01" and c.subject == "语文"]
    if c01:
        print("--- C01语文 locked to:", c01[0].teacher_candidates,
              "locked=", c01[0].locked)
    return p


print("== 场景1：双通道全量 ==")
prepare()
load_and_validate()

print("\n== 场景2：坏 JSON 围栏（应告警不致命） ==")
prepare(with_file=False,
        fence="```json\n{broken json!!!}\n```\n张卫东教C05数学\n")
load_and_validate()

print("\n== 场景3：全员禁连堂（应 DataError） ==")
# 数学连堂固定周二；把 13 位数学教师全部禁周二连堂 → 阶段2应拒绝
math_teachers = ["T014", "T015", "T016", "T017", "T018", "T019", "T020",
                 "T021", "T022", "T023", "T024", "T025", "T026"]
cons = [{"type": "teacher_no_double_day", "teacher": t, "days": [2]}
        for t in math_teachers]
prepare(with_file=False, fence="```json\n" +
        json.dumps({"version": 1, "constraints": cons}, ensure_ascii=False) +
        "\n```\n")
try:
    load_and_validate()
except DataError as e:
    # 加载层候选收缩（apply_structured_double_bans）抢先拒绝，同样是预期路径
    print("  DataError（符合预期，加载层候选收缩）:", e)
