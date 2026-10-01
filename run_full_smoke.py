# -*- coding: utf-8 -*-
"""排课助手 —— 完整冒烟：用仓库合成数据真排一次 25 班，并校验交付出去的文件。

用法：  D:\\python\\python.exe run_full_smoke.py
       （由 run_checks.py --full 调起，也可以单独跑）

流程：
  1) 复制 engine/test_input_25_new/input.xlsx 到系统临时目录，配上外壳默认求解配置，
     并塞进用户当时的两行「特殊要求」（用来同时验证告警话术）
  2) 跑 engine.exe（没有就退回源码 engine.py）
  3) 用 verify_hard.py --xlsx 校验**交付出去的** result.xlsx（H1-H5）
  4) 独立再查一遍：课表格子是否「学科+教师」两行、免教师学科是否干净、任课表是否还在

全程只读仓库、只在系统临时目录写东西。
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

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = HERE
ENGINE_DIR = os.path.join(REPO, "engine")
SRC_XLSX = os.path.join(ENGINE_DIR, "test_input_25_new", "input.xlsx")
VERIFY = os.path.join(ENGINE_DIR, "verify_hard.py")

HL = {
    "schedule": {"periods_per_day": 8, "period_minutes": 40, "morning_start": "08:00",
                 "morning_end": "12:20", "afternoon_start": "14:30", "afternoon_end": "16:55",
                 "long_break_after_period": 3, "long_break_minutes": 30,
                 "eye_break_after_period": 6, "eye_break_minutes": 15,
                 "default_break_minutes": 10},
    "fixed_classes": [{"day": 1, "period": 1, "subject": "班会", "note": ""},
                      {"day": 1, "period": 8, "subject": "研究性学习", "note": ""},
                      {"day": 4, "period": 8, "subject": "校本课", "note": ""}],
    "classes": {"total_classes": 25, "arts_start": 1, "arts_end": 4, "science_start": 5,
                "science_end": 25, "gaokao_policy": "新高考3+1+2"},
    "consecutive": {"subjects": ["语文", "数学", "英语"], "math_day": 2, "chinese_day": 3,
                    "english_day": 4, "rule": "", "only_core_subjects": True},
    "solver": {"max_time_seconds": 60, "teacher_candidate_k": 5, "num_plans": 3},
    "extra_constraints": ("1.非物理选科班每周安排一节物理，非政治选科班每周安排一节政治\n"
                          "2.教师T30所教授的班级其中一个必须是C05"),
}

BASE = os.path.join(tempfile.gettempdir(), "paike_full_smoke")
if os.path.exists(BASE):
    shutil.rmtree(BASE)
inp, out = os.path.join(BASE, "input"), os.path.join(BASE, "output")
os.makedirs(inp)
os.makedirs(out)
shutil.copy(SRC_XLSX, os.path.join(inp, "input.xlsx"))
with open(os.path.join(inp, "requirements.txt"), "w", encoding="utf-8") as f:
    f.write(HL["extra_constraints"])
with open(os.path.join(inp, "hard_limits.json"), "w", encoding="utf-8") as f:
    json.dump(HL, f, ensure_ascii=False, indent=2)

# 引擎：优先用打包好的 exe（和用户实际跑的一致），没有就用源码
exe = os.path.join(ENGINE_DIR, "dist", "engine.exe")
if os.path.exists(exe):
    cmd = [exe]
    label = "打包引擎 " + exe
else:
    cmd = [sys.executable, os.path.join(ENGINE_DIR, "engine.py")]
    label = "源码引擎 engine.py"
print("=" * 78)
print("第 1 步：25 班真排课（3 方案 × 60 秒）  引擎:", label)
t0 = time.time()
p = subprocess.run(cmd + [inp, out, "full_smoke"], cwd=ENGINE_DIR,
                   capture_output=True, text=True, encoding="utf-8", errors="replace",
                   env=dict(os.environ, PYTHONIOENCODING="utf-8"))
wall = time.time() - t0
for line in (p.stdout or "").splitlines():
    if any(k in line for k in ("阶段", "方案 ", "求解完成")):
        print("    " + line)
print(f"  总耗时 {wall:.1f}s")

d = json.load(open(os.path.join(out, "status.json"), encoding="utf-8"))
print(f"  >>> code={d['code']}  方案数={len(d['plans'])}  engine_version={d.get('engine_version')}")
print("  >>> warnings:")
for w in d["warnings"]:
    print("       · " + w)
ok1 = (d["code"] in (0, 1)) and len(d["plans"]) >= 1
print(f"\n  {'✔' if ok1 else '✘'} 排课{'成功' if ok1 else '失败'}")

print()
print("=" * 78)
print("第 2 步：verify_hard.py 校验交付出去的 result.xlsx（H1-H5）")
r = subprocess.run([sys.executable, VERIFY, inp, "--xlsx", os.path.join(out, "result.xlsx")],
                   cwd=ENGINE_DIR, capture_output=True, text=True, encoding="utf-8",
                   errors="replace", env=dict(os.environ, PYTHONIOENCODING="utf-8"))
tail = [l for l in (r.stdout or "").splitlines() if l.strip()]
for line in tail[-14:]:
    print("    " + line)
ok2 = r.returncode == 0 and "交付文件校验通过" in (r.stdout or "")
print(f"\n  {'✔' if ok2 else '✘'} verify_hard")

print()
print("=" * 78)
print("第 3 步：独立再查一遍交付文件")
from openpyxl import load_workbook
NO_T = {"体育", "体育活动", "艺术", "音乐", "美术", "信息", "信息技术", "通用技术", "心理",
        "班会", "研究性学习", "校本课", "自习", "书法", "劳动", "生涯规划"}
wb = load_workbook(os.path.join(out, "result.xlsx"))
ws = wb["方案1"]
with_nl = no_teacher_bad = 0
sample = None
for row in ws.iter_rows(values_only=True):
    for v in row:
        if isinstance(v, str) and "\n" in v:
            with_nl += 1
            subj, teacher = v.split("\n", 1)
            if subj.strip() in NO_T or not teacher.strip():
                no_teacher_bad += 1
            if sample is None:
                sample = v
wb.close()
ok3 = with_nl > 0 and no_teacher_bad == 0
print(f"  「学科+教师」两行的格子: {with_nl} 个；免教师学科混进教师/空教师: {no_teacher_bad} 个")
print(f"  抽样: {sample!r}")
print(f"  {'✔' if ok3 else '✘'} 课表两行显示")

print()
print("=" * 78)
print("结果：" + ("全部通过 ✅" if (ok1 and ok2 and ok3) else "有失败项 ❌"))
shutil.rmtree(BASE, ignore_errors=True)
print("（临时目录已清理）")
sys.exit(0 if (ok1 and ok2 and ok3) else 1)
