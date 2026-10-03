# -*- coding: utf-8 -*-
"""排课助手 —— 一键回归检查

用法（任选其一）：
    双击仓库根目录的「一键检查.bat」
    或命令行：  D:\\python\\python.exe run_checks.py
    完整冒烟（多花约 3 分钟，真排一次 25 班）：  python run_checks.py --full

全部只读仓库、只在系统临时目录里写东西，不会改任何仓库文件。
检查项：
  1) 「特殊要求」解析：行首编号 / 普通文字 / 暂不支持的写法   （prefix_probe）
  2) 「信息课」等免教师学科 + 归一化 + 一次真实小排课        （issue_probe）
  3) 约束转换器：自然语言 → 结构化约束的词典自测             （约束转换器/测试语料/自测.py）
  4) 打包好的 engine.exe 健壮性：异常不弹框、都写 status.json （guard_test）
  5) --full：25 班完整排课 + verify_hard 校验交付文件
"""
import io
import os
import subprocess
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))   # 本文件所在目录：仓库根/工具/
REPO = os.path.dirname(HERE)                        # 仓库根
FULL = "--full" in sys.argv

SCRIPTS_DIR_1 = os.path.join(REPO, "测试存档", "2026-10-01-用户实测问题复现", "复现脚本")
SCRIPTS_DIR_2 = os.path.join(REPO, "测试存档", "2026-10-01-引擎异常兜底加固", "复现脚本")
ENGINE_EXE = os.path.join(REPO, "engine", "dist", "engine.exe")

results = []


def run_script(title, path, args=(), expect=(), forbid=("Traceback", "ValueError")):
    """跑一个脚本，按 expect 里的关键词判定通过。"""
    print("\n" + "=" * 78)
    print("▶ " + title)
    print("-" * 78)
    if not os.path.exists(path):
        results.append((title, False, "脚本不存在: " + path))
        print("  ✘ 脚本不存在:", path)
        return
    p = subprocess.run([sys.executable, path, *args], capture_output=True,
                       text=True, encoding="utf-8", errors="replace",
                       env=dict(os.environ, PYTHONIOENCODING="utf-8"),
                       cwd=os.path.dirname(path) or ".")
    out = (p.stdout or "") + (p.stderr or "")
    print(out.rstrip()[-3000:])
    problems = [w for w in forbid if w in out]
    missing = [w for w in expect if w not in out]
    if p.returncode != 0 or problems or missing:
        why = []
        if p.returncode != 0:
            why.append(f"退出码 {p.returncode}")
        if problems:
            why.append("出现了不该有的字样: " + "、".join(problems))
        if missing:
            why.append("没找到应有字样: " + "、".join(missing))
        results.append((title, False, "；".join(why)))
        print(f"\n  ✘ 未通过：{'；'.join(why)}")
    else:
        results.append((title, True, ""))
        print("\n  ✔ 通过")


print("=" * 78)
print("排课助手 · 一键回归检查" + ("（含完整冒烟）" if FULL else "（快速档）"))
print("Python:", sys.executable)

# 1) 特殊要求解析
run_script(
    "1) 「特殊要求」解析（行首编号 / 普通文字 / 暂不支持）",
    os.path.join(SCRIPTS_DIR_1, "prefix_probe.py"),
    expect=["✔ 生效", "静默"],
)

# 2) 信息课 + 归一化 + 真实小排课
run_script(
    "2) 「信息课」与免教师学科（含一次真实排课）",
    os.path.join(SCRIPTS_DIR_1, "issue_probe.py"),
    expect=["code=1", "归一化"],
)

# 3) 约束转换器词典自测（自然语言 → 结构化约束）
run_script(
    "3) 约束转换器词典自测（自然语言 → 结构化约束）",
    os.path.join(REPO, "约束转换器", "测试语料", "自测.py"),
    expect=["自测通过"],
    forbid=("Traceback", "ValueError", "自测未通过"),
)

# 4) 打包引擎的健壮性
run_script(
    "4) engine.exe 健壮性（异常不弹框、写出 status.json）",
    os.path.join(SCRIPTS_DIR_2, "guard_test.py"),
    args=[ENGINE_EXE],
    expect=["全部完成"],
    forbid=("Traceback", "ValueError", "又弹模态框"),
)

# 5) 可选：完整冒烟
if FULL:
    run_script(
        "5) 25 班完整排课 + verify_hard 校验交付文件",
        os.path.join(HERE, "run_full_smoke.py"),
        expect=["交付文件校验通过"],
    )
else:
    print("\n（提示：要跑约 3 分钟的完整冒烟，请执行：python run_checks.py --full）")

# ---- 汇总 ----
print("\n" + "=" * 78)
print("结果汇总")
print("-" * 78)
ok = True
for title, passed, why in results:
    mark = "✔" if passed else "✘"
    print(f"  {mark} {title}" + ("" if passed else f"   ← {why}"))
    ok = ok and passed
print("-" * 78)
print("全部通过 ✅" if ok else "有失败项 ❌ —— 把上面的输出发给技术支持即可")
sys.exit(0 if ok else 1)
