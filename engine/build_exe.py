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
        "--name", "engine",
        "--clean",
        "--noconfirm",
        "--collect-submodules", "scheduler",
        # 关键：补齐 ortools/.libs 下的原生 DLL，缺了 exe 跑不起来
        "--collect-all", "ortools",
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
