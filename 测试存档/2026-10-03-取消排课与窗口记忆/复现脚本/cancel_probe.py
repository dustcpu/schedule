# -*- coding: utf-8 -*-
"""验证「取消排课」的机制在 Windows 上真的有效 —— 这是本轮改动里唯一
无法靠"看代码"确认的部分。

要验证的三个假设：
  1) PyInstaller onefile 的引擎在 Windows 上是「bootloader 父进程 + 子进程」两个进程
  2) taskkill /F /T 能把整棵树杀掉（只杀父进程会留下子进程继续算）
  3) 强杀后 %TEMP% 里会留下 _MEI 解包目录 —— 确认外壳的兜底清理确有必要

做法完全模拟外壳：spawn 引擎 → 等它进入求解 → taskkill /F /T → 看进程是否清空。
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

# 用相对位置定位仓库根（脚本在 测试存档/<日期-主题>/复现脚本/ 下），
# 不写死绝对路径；工作区放系统临时目录，不污染仓库。
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
EXE = os.path.join(REPO, "engine", "dist", "engine.exe")
SRC = os.path.join(REPO, "engine", "test_input_25_new", "input.xlsx")
BASE = os.path.join(tempfile.gettempdir(), "paike_cancel")


def engine_procs():
    """当前所有名为 engine.exe 的进程 PID。"""
    r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq engine.exe", "/NH"],
                       capture_output=True, text=True, errors="replace")
    pids = set()
    for line in (r.stdout or "").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].lower() == "engine.exe":
            pids.add(parts[1])
    return pids


def mei_dirs():
    """%TEMP% 下的 _MEI* 解包目录。"""
    tmp = os.environ.get("TEMP") or r"C:\Windows\Temp"
    out = []
    for n in os.listdir(tmp):
        if n.startswith("_MEI") and os.path.isdir(os.path.join(tmp, n)):
            out.append(n)
    return set(out)


# ---- 准备输入（会真跑 25 班，方便中途取消）----
w = os.path.join(BASE, "work")
if os.path.exists(w):
    shutil.rmtree(w, ignore_errors=True)
os.makedirs(os.path.join(w, "input"))
os.makedirs(os.path.join(w, "output"))
shutil.copy(SRC, os.path.join(w, "input", "input.xlsx"))
open(os.path.join(w, "input", "requirements.txt"), "w", encoding="utf-8").write("")
HL = {
    "schedule": {"periods_per_day": 8, "period_minutes": 40, "morning_start": "08:00",
                 "morning_end": "12:20", "afternoon_start": "14:30", "afternoon_end": "16:55",
                 "long_break_after_period": 3, "long_break_minutes": 30,
                 "eye_break_after_period": 6, "eye_break_minutes": 15, "default_break_minutes": 10},
    "fixed_classes": [{"day": 1, "period": 1, "subject": "班会", "note": ""},
                      {"day": 1, "period": 8, "subject": "研究性学习", "note": ""},
                      {"day": 4, "period": 8, "subject": "校本课", "note": ""}],
    "classes": {"total_classes": 25, "arts_start": 1, "arts_end": 4,
                "science_start": 5, "science_end": 25, "gaokao_policy": "新高考3+1+2"},
    "consecutive": {"subjects": ["语文", "数学", "英语"], "math_day": 2, "chinese_day": 3,
                    "english_day": 4, "rule": "", "only_core_subjects": True},
    "solver": {"max_time_seconds": 300, "teacher_candidate_k": 5, "num_plans": 3},
    "extra_constraints": ""
}
json.dump(HL, open(os.path.join(w, "input", "hard_limits.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=2)

mei_before = mei_dirs()
print("=" * 72)
print("启动前：engine.exe 进程 %s，_MEI 目录 %d 个" % (sorted(engine_procs()) or "无", len(mei_before)))

# ---- 启动（完全照外壳的方式：管道接走 stdout/stderr）----
t0 = time.time()
p = subprocess.Popen([EXE, os.path.join(w, "input"), os.path.join(w, "output"), "cancel_probe"],
                     stdout=subprocess.PIPE, stderr=subprocess.PIPE)
print("已启动，PID=%d，等待它进入求解阶段…" % p.pid)

# 沙箱里 onefile 解包较慢，给足时间；期间打印进度便于判断
deadline = time.time() + 150
seen_two = False
while time.time() < deadline:
    time.sleep(5)
    ps = engine_procs()
    n_line = ""
    if len(ps) >= 2 and not seen_two:
        seen_two = True
        n_line = "   <-- 出现两个 engine.exe：确认 onefile 是『父+子』双进程"
    print("  +%3ds  进程数=%d %s%s" % (int(time.time() - t0), len(ps), sorted(ps), n_line))
    if seen_two and time.time() - t0 > 80:
        break

procs_before_kill = engine_procs()
print()
print("=" * 72)
print("杀掉之前：engine.exe 进程 = %s （共 %d 个）" % (sorted(procs_before_kill), len(procs_before_kill)))

# ---- 模拟 cancel_scheduler：taskkill /F /T /PID <父进程> ----
print("执行 taskkill /F /T /PID %d" % p.pid)
r = subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)],
                   capture_output=True, text=True, errors="replace")
print("  退出码=%s" % r.returncode)
for line in (r.stdout or "").strip().splitlines():
    print("  " + line.strip())

time.sleep(4)
procs_after = engine_procs()
mei_after = mei_dirs()
new_mei = mei_after - mei_before

print()
print("=" * 72)
print("结果")
print("  taskkill 后 engine.exe 进程：%s" % (sorted(procs_after) or "无"))
print("  进程树是否杀干净：%s" % ("[OK] 是" if not procs_after
                                 else "[X] 否，残留 %s" % sorted(procs_after)))
print("  新增 _MEI 解包目录：%s" % (sorted(new_mei) or "无"))
if new_mei:
    total = 0
    tmp = os.environ.get("TEMP")
    for n in new_mei:
        for dp, _, fs_ in os.walk(os.path.join(tmp, n)):
            for f in fs_:
                try:
                    total += os.path.getsize(os.path.join(dp, f))
                except OSError:
                    pass
    print("     合计约 %.1f MB —— 确认外壳的 cleanup_mei_leftovers 确有必要"
          % (total / 1048576.0))

p.wait(timeout=30)
print("  spawn 的父进程已回收，退出码=%s" % p.returncode)
print("=" * 72)
