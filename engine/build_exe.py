# -*- coding: utf-8 -*-
"""排课引擎打包脚本 —— 生成 engine.exe（Tauri 外壳的 sidecar）

用法：
    python build_exe.py

前置条件（当前 Python 环境需已安装）：
    pip install ortools openpyxl reportlab pyinstaller

产物：
    engine/dist/engine.exe

部署方式：
    把 dist/engine.exe 复制到 src-tauri/resources/engine/engine.exe，
    这样 `cargo tauri build` 才能通过 tauri.conf.json 的
    bundle.resources = ["resources/*"] 把它打进安装包。

踩过的坑（务必保留这条注释）：
    ortools 的原生 DLL 藏在 ortools/.libs/ 目录下（ortools.dll、
    libprotobuf.dll、abseil_dll.dll 等，合计约 72MB），PyInstaller 默认
    解析不到，会报 "Library not found: could not resolve 'ortools.dll'"。
    若不处理，打出来的 exe 能生成但一运行就崩：
        ImportError: DLL load failed while importing cp_model_helper
    解决：加 --collect-all ortools，把 .libs 下的 DLL 一并收集。
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def main() -> int:
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        # 协议 §2 / §10：引擎进程不允许弹出任何 GUI 或控制台窗口
        "--noconsole",
        "--name", "engine",
        "--clean",
        "--noconfirm",
        "--collect-submodules", "scheduler",
        # 关键：补齐 ortools/.libs 下的原生 DLL，缺了 exe 跑不起来
        "--collect-all", "ortools",
        # ⚠️ 必须显式声明：engine.py 只在 _run_validate() 的函数体里 import 它，
        #    PyInstaller 的静态分析未必收得到。漏了它，外壳调用 `engine --validate`
        #    时会在安装版报 ModuleNotFoundError，而开发版（直接跑 .py）照样正常，
        #    属于典型的"只有打包后才暴露"的坑。
        "--hidden-import", "validate_input",
        "engine.py",
    ]
    print("[build_exe] 工作目录:", HERE)
    print("[build_exe] 命令:", " ".join(cmd))
    result = subprocess.run(cmd, cwd=HERE)
    if result.returncode != 0:
        print("[build_exe] 打包失败，退出码:", result.returncode)
        return result.returncode

    exe = os.path.join(HERE, "dist", "engine.exe")
    if os.path.exists(exe):
        size = os.path.getsize(exe)
        print(f"[build_exe] 打包成功: {exe}")
        print(f"[build_exe] 体积: {size} bytes ≈ {size / 1048576:.2f} MB")
        print("[build_exe] 下一步: 复制到 src-tauri/resources/engine/engine.exe")
    else:
        print("[build_exe] 未找到产物 engine.exe")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
