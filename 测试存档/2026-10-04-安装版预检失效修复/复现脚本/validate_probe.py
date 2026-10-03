# -*- coding: utf-8 -*-
"""验证 `engine --validate` 子命令（安装版输入预检所依赖的那条路径）。

用法（在仓库任意位置执行均可）：
    python validate_probe.py          # 用源码 engine.py（快）
    python validate_probe.py --exe    # 用打包后的 engine/dist/engine.exe（慢，但才是发布形态）

只读仓库、只打印，不修改任何文件。用仓库自带合成数据 engine/test_input_25_new/。
"""
import io
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))   # 仓库根
ENGINE_DIR = os.path.join(ROOT, "engine")
SAMPLE = os.path.join(ENGINE_DIR, "test_input_25_new", "input.xlsx")


def run(argv, cwd):
    p = subprocess.run(argv, cwd=cwd, stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE)
    return p.returncode, p.stdout, p.stderr


def parse(stdout):
    return json.loads(stdout.decode("utf-8"))


def main() -> int:
    use_exe = "--exe" in sys.argv
    if use_exe:
        engine = os.path.join(ENGINE_DIR, "dist", "engine.exe")
        base = [engine]
        if not os.path.exists(engine):
            print("[skip] 没找到 %s，先跑 engine/build_exe.py" % engine)
            return 1
    else:
        base = [sys.executable, os.path.join(ENGINE_DIR, "engine.py")]

    if not os.path.exists(SAMPLE):
        print("[skip] 没找到合成数据 %s" % SAMPLE)
        return 1

    ok = True

    # 1) 正常输入
    rc, out, err = run(base + ["--validate", SAMPLE], ENGINE_DIR)
    d = parse(out)
    tids = d["stats"].get("teacher_map", {})
    cids = d["stats"].get("class_map", {})
    print("[1] 正常输入  valid=%s teachers=%s classes=%s subjects=%s "
          "teacher_map=%d class_map=%d"
          % (d["valid"], d["stats"].get("teachers"), d["stats"].get("classes"),
             d["stats"].get("subjects"), len(tids), len(cids)))
    print("    format=%s" % d["stats"].get("format"))
    if not d["valid"] or not tids or not cids:
        ok = False
        print("    !! 期望 valid=True 且两张映射非空")
    if err.strip():
        print("    note stderr: %s" % err.decode("utf-8", "replace").strip()[:200])

    # 2) 文件不存在
    rc, out, err = run(base + ["--validate", "no_such_file.xlsx"], ENGINE_DIR)
    d2 = parse(out)
    print("[2] 文件不存在 valid=%s errors=%s" % (d2["valid"], d2["errors"]))
    if d2["valid"] or not d2["errors"]:
        ok = False
        print("    !! 期望 valid=False 且 errors 非空")

    # 3) 不带参数：应仍是「排课」入口（打印版本行），不受子命令影响
    rc, out, err = run(base, ENGINE_DIR)
    text = (out + err).decode("utf-8", "replace")
    first = text.splitlines()[0] if text.splitlines() else ""
    print("[3] 无参数      第一行=%r" % first)
    if "排课引擎" not in first:
        ok = False
        print("    !! 期望第一行是版本号（排课入口未被破坏）")

    print("=> %s" % ("全部通过" if ok else "有失败项"))
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
