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
import traceback

# 保证同目录下的 scheduler 包可被 import（PyInstaller 打包后同样成立）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from scheduler import __version__ as ENGINE_VERSION
from scheduler.data.load import load_problem, DataError
from scheduler.data.validate import validate
from scheduler.solver.model import build_model
from scheduler.solver.solve import solve_plans
from scheduler.export import write_xlsx, write_pdf, write_status


def _fail(out_dir: str, task_id: str, message: str, warnings=None) -> None:
    """失败路径：写 status.json（code=2），不写结果文件。"""
    try:
        os.makedirs(out_dir, exist_ok=True)
        write_status(os.path.join(out_dir, "status.json"), task_id, 2,
                     message, [], warnings or [])
    except Exception as e:
        print(f"[engine] 写 status.json 失败：{e}", file=sys.stderr)
        print(traceback.format_exc(), file=sys.stderr)


def main() -> int:
    # 协议 §10：版本号打印到 stdout 第一行
    print(f"排课引擎 v{ENGINE_VERSION}")

    if len(sys.argv) < 4:
        print("用法: engine <输入目录> <输出目录> <任务ID>", file=sys.stderr)
        return 0  # 进程退出码恒 0（协议 §6），但无输出目录无法写 status.json

    in_dir, out_dir, task_id = sys.argv[1], sys.argv[2], sys.argv[3]

    try:
        os.makedirs(out_dir, exist_ok=True)
    except Exception as e:
        print(f"[engine] 输出目录不可写：{e}", file=sys.stderr)
        return 0

    try:
        problem = load_problem(in_dir)
    except DataError as e:
        _fail(out_dir, task_id, f"数据错误：{e}")
        return 0
    except Exception as e:
        print(traceback.format_exc(), file=sys.stderr)
        _fail(out_dir, task_id, f"读取输入失败：{e}")
        return 0

    warnings = list(problem.warnings)

    try:
        warnings.extend(validate(problem))
    except DataError as e:
        _fail(out_dir, task_id, f"数据校验未通过：{e}", warnings)
        return 0
    except Exception as e:
        print(traceback.format_exc(), file=sys.stderr)
        _fail(out_dir, task_id, f"数据校验出错：{e}", warnings)
        return 0

    try:
        bundle = build_model(problem)
        plans, solve_warnings, status = solve_plans(bundle)
        warnings.extend(solve_warnings)
    except Exception as e:
        print(traceback.format_exc(), file=sys.stderr)
        _fail(out_dir, task_id, f"求解过程出错：{e}", warnings)
        return 0

    if not plans:
        if status == "INFEASIBLE":
            msg = ("约束互相冲突，排不出可行课表。"
                   "常见原因：固定课与连堂冲突、教师带班过多导致同时段撞课、"
                   "某班周课时超过可用格数。请检查输入数据或放宽固定课/连堂设置。")
        elif status == "UNKNOWN":
            msg = "在时限内未能求出可行课表（可能规模过大），请减少班级数或放宽约束后重试。"
        else:
            msg = "未能生成任何可行方案。"
        _fail(out_dir, task_id, msg, warnings)
        return 0

    # 成功：写三件套
    try:
        write_xlsx(os.path.join(out_dir, "result.xlsx"), plans, problem)
        write_pdf(os.path.join(out_dir, "result.pdf"), task_id, plans, problem,
                  problem.requirements)
    except Exception as e:
        print(traceback.format_exc(), file=sys.stderr)
        _fail(out_dir, task_id, f"写出结果文件失败：{e}", warnings)
        return 0

    code = 1 if warnings else 0
    message = f"排课完成，共生成 {len(plans)} 套方案"
    write_status(os.path.join(out_dir, "status.json"), task_id, code, message,
                 plans, warnings)

    print(f"[engine] {message}，code={code}，warnings={len(warnings)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
