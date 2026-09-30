# -*- coding: utf-8 -*-
"""排课助手 — 排课引擎（真算法，替换 mock_engine.py 的占位实现）

用法（由 Tauri 外壳自动调用，见接口协议 §2）：
    engine.exe <输入目录> <输出目录> <任务ID>

流程：load → validate → build(CP-SAT) → solve(多解) → export
输出：result.xlsx / result.pdf / status.json（协议 §4）
进程退出码恒为 0，业务结果一律以 status.json 的 code 表达（协议 §6）。
"""
import os
import sys
import time
import traceback
from datetime import datetime

# 保证同目录下的 scheduler 包可被 import（PyInstaller 打包后同样成立）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 让 stdout / stderr 一律按 UTF-8 输出。
# 外壳是 GUI 程序、没有控制台，Python 往管道写时会退回系统的 ANSI 代码页
# （简体中文 Windows = cp936/GBK），而外壳按 UTF-8 解码 → 日志里的中文全是乱码。
# ⚠️ 实测 PyInstaller 打包后的程序**会忽略** PYTHONIOENCODING / PYTHONUTF8，
#    所以必须在这里直接改流编码，不能只靠外部设环境变量。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from scheduler import __version__ as ENGINE_VERSION
from scheduler.data.load import load_problem, DataError
from scheduler.data.validate import validate
from scheduler.solver.model import build_model
from scheduler.solver.solve import solve_plans
from scheduler.export import write_xlsx, write_pdf, write_status


# ==================== 日志 ====================

_log_file = None
_start_time = time.time()


def elog(msg: str) -> None:
    """写日志到 stdout 和输出目录的 engine.log"""
    global _log_file
    elapsed = time.time() - _start_time
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts} +{elapsed:6.1f}s] {msg}"
    print(line, flush=True)
    if _log_file:
        try:
            with open(_log_file, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass


def _fail(out_dir: str, task_id: str, message: str, warnings=None) -> None:
    """失败路径：写 status.json（code=2），不写结果文件。"""
    try:
        os.makedirs(out_dir, exist_ok=True)
        write_status(os.path.join(out_dir, "status.json"), task_id, 2,
                     message, [], warnings or [])
        elog(f"失败: {message}")
    except Exception as e:
        print(f"[engine] 写 status.json 失败：{e}", file=sys.stderr)
        print(traceback.format_exc(), file=sys.stderr)


def _crash(e: Exception, what: str) -> str:
    """把内部异常转成教务老师看得懂的话术。

    异常原文与 traceback 只打到 stderr（进外壳日志），不出现在 UI 上；
    同时生成一个错误编号，便于用户报障时回溯。
    """
    import random
    code = f"E-{time.strftime('%Y%m%d-%H%M%S')}-{random.randint(0, 0xffff):04x}"
    print(f"[engine] {code} {what}失败 {type(e).__name__}: {e}", file=sys.stderr)
    print(traceback.format_exc(), file=sys.stderr)
    elog(f"崩溃 {code}: {what}失败 {type(e).__name__}: {e}")
    elog(traceback.format_exc())
    return (f"{what}时引擎内部出错，没能排出课表。请稍后重试；"
            f"若反复出现，请把输入的 Excel 发给技术支持并附上错误编号 {code}。")


def main() -> int:
    global _log_file, _start_time
    _start_time = time.time()

    # 协议 §10：版本号打印到 stdout 第一行
    print(f"排课引擎 v{ENGINE_VERSION}")

    if len(sys.argv) < 4:
        print("用法: engine <输入目录> <输出目录> <任务ID>", file=sys.stderr)
        return 0  # 进程退出码恒 0（协议 §6），但无输出目录无法写 status.json

    in_dir, out_dir, task_id = sys.argv[1], sys.argv[2], sys.argv[3]

    try:
        os.makedirs(out_dir, exist_ok=True)
        _log_file = os.path.join(out_dir, "engine.log")
        # 清空旧日志
        with open(_log_file, "w", encoding="utf-8") as f:
            f.write(f"排课引擎 v{ENGINE_VERSION} 启动\n")
            f.write(f"输入目录: {in_dir}\n")
            f.write(f"输出目录: {out_dir}\n")
            f.write(f"任务ID: {task_id}\n")
            f.write("=" * 60 + "\n")
    except Exception as e:
        print(f"[engine] 输出目录不可写：{e}", file=sys.stderr)
        return 0

    elog("=== 引擎启动 ===")
    elog(f"输入目录: {in_dir}")
    elog(f"输出目录: {out_dir}")

    # 阶段1: 加载输入
    elog("阶段1/5: 加载输入文件...")
    t0 = time.time()
    try:
        problem = load_problem(in_dir)
        elog(f"  加载完成, 耗时{time.time()-t0:.1f}s")
        elog(f"  教师数: {len(problem.teachers)}, 班级数: {len(problem.classes)}, 课程数: {len(problem.courses)}")
    except DataError as e:
        elog(f"  数据错误: {e}")
        _fail(out_dir, task_id, f"数据错误：{e}")
        return 0
    except Exception as e:
        _fail(out_dir, task_id, _crash(e, "读取输入"))
        return 0

    warnings = list(problem.warnings)
    for w in warnings:
        elog(f"  警告: {w}")

    # 阶段2: 数据校验
    elog("阶段2/5: 数据校验...")
    t0 = time.time()
    try:
        validate_warnings = validate(problem)
        warnings.extend(validate_warnings)
        elog(f"  校验完成, 耗时{time.time()-t0:.1f}s, 新增警告{len(validate_warnings)}条")
    except DataError as e:
        elog(f"  校验失败: {e}")
        _fail(out_dir, task_id, f"数据校验未通过：{e}", warnings)
        return 0
    except Exception as e:
        _fail(out_dir, task_id, _crash(e, "数据校验"), warnings)
        return 0

    # 阶段3: 构建模型
    elog("阶段3/5: 构建CP-SAT模型...")
    t0 = time.time()
    try:
        bundle = build_model(problem)
        elog(f"  模型构建完成, 耗时{time.time()-t0:.1f}s")
        elog(f"  变量数: {len(bundle.model.Proto().variables)}, 约束数: {len(bundle.model.Proto().constraints)}")
    except Exception as e:
        _fail(out_dir, task_id, _crash(e, "构建模型"), warnings)
        return 0

    # 阶段4: 求解
    elog("阶段4/5: 求解排课方案...")
    t0 = time.time()
    try:
        plans, solve_warnings, status = solve_plans(bundle)
        warnings.extend(solve_warnings)
        elog(f"  求解完成, 耗时{time.time()-t0:.1f}s, 状态={status}, 方案数={len(plans)}")
        for i, p in enumerate(plans):
            elog(f"  方案{i+1}: {p.name}, 评分={p.score:.1f}")
    except Exception as e:
        _fail(out_dir, task_id, _crash(e, "排课求解"), warnings)
        return 0

    if not plans:
        if status == "INFEASIBLE":
            msg = ("约束互相冲突，排不出可行课表。"
                   "常见原因：固定课与连堂冲突、教师带班过多导致同时段撞课、"
                   "某班周课时超过可用格数。请检查输入数据或放宽固定课/连堂设置。")
        elif status == "UNKNOWN":
            msg = ("在时限内没能排完（班级或课程较多）。"
                   "建议减少方案套数，或放宽连堂/固定课设置后重试。")
        else:
            msg = "未能生成任何可行方案。"
        elog(f"无可行方案, status={status}")
        _fail(out_dir, task_id, msg, warnings)
        return 0

    # 阶段5: 导出结果
    elog("阶段5/5: 导出结果文件...")
    t0 = time.time()
    try:
        write_xlsx(os.path.join(out_dir, "result.xlsx"), plans, problem)
        elog(f"  Excel导出完成, 耗时{time.time()-t0:.1f}s")
        t0 = time.time()
        write_pdf(os.path.join(out_dir, "result.pdf"), task_id, plans, problem,
                  problem.requirements)
        elog(f"  PDF导出完成, 耗时{time.time()-t0:.1f}s")
    except Exception as e:
        _fail(out_dir, task_id, _crash(e, "写出结果文件"), warnings)
        return 0

    code = 1 if warnings else 0
    message = f"排课完成，共生成 {len(plans)} 套方案"
    write_status(os.path.join(out_dir, "status.json"), task_id, code, message,
                 plans, warnings)

    elog(f"=== 排课完成 ===")
    elog(f"总耗时: {time.time()-_start_time:.1f}s")
    elog(f"方案数: {len(plans)}, 警告数: {len(warnings)}, code={code}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
