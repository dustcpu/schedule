# -*- coding: utf-8 -*-
"""排课助手 — 排课引擎（真算法，替换 mock_engine.py 的占位实现）

用法（由 Tauri 外壳自动调用，见接口协议 §2）：
    engine.exe <输入目录> <输出目录> <任务ID>

流程：load → validate → build(CP-SAT) → solve(多解) → export
输出：result.xlsx / result.pdf / status.json（协议 §4）
进程退出码恒为 0，业务结果一律以 status.json 的 code 表达（协议 §6）。
"""
import json
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


def eout(msg: str) -> None:
    """往 stdout 写一行，失败也不抛。

    ⚠️ 打包成 --noconsole 的 exe 后，stdout 可能没有有效句柄；
    调用方（外壳）若提前关闭了管道，写操作会抛 OSError [Errno 22]。
    这里一旦让异常逃出去，PyInstaller 就会弹模态框把进程卡住等人点 Close，
    而外壳正等着这个进程退出 —— 所以输出失败必须就地吞掉。
    """
    try:
        print(msg, flush=True)
    except Exception:
        pass


def eerr(msg: str) -> None:
    """往 stderr 写一行，失败也不抛（理由同 eout）。"""
    try:
        print(msg, file=sys.stderr, flush=True)
    except Exception:
        pass


def elog(msg: str) -> None:
    """写日志到 stdout 和输出目录的 engine.log（两条路都不能抛）"""
    global _log_file
    elapsed = time.time() - _start_time
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts} +{elapsed:6.1f}s] {msg}"
    eout(line)
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
        eerr(f"[engine] 写 status.json 失败：{e}")
        eerr(traceback.format_exc())


def _crash(e: Exception, what: str) -> str:
    """把内部异常转成教务老师看得懂的话术。

    异常原文与 traceback 只打到 stderr（进外壳日志），不出现在 UI 上；
    同时生成一个错误编号，便于用户报障时回溯。
    """
    import random
    code = f"E-{time.strftime('%Y%m%d-%H%M%S')}-{random.randint(0, 0xffff):04x}"
    eerr(f"[engine] {code} {what}失败 {type(e).__name__}: {e}")
    eerr(traceback.format_exc())
    elog(f"崩溃 {code}: {what}失败 {type(e).__name__}: {e}")
    elog(traceback.format_exc())
    return (f"{what}时引擎内部出错，没能排出课表。请稍后重试；"
            f"若反复出现，请把输入的 Excel 发给技术支持并附上错误编号 {code}。")


# ==================== 子命令：输入预检 ====================

def _emit_json(payload) -> None:
    """按 UTF-8 往 stdout 写 JSON（不依赖 locale / 环境变量，理由同 validate_input._emit）。"""
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    buf = getattr(sys.stdout, "buffer", None)
    if buf is not None:
        buf.write(data)
        buf.flush()
    elif sys.stdout is not None:
        sys.stdout.write(data.decode("utf-8", "replace"))


def _run_validate(argv) -> int:
    """子命令 `engine --validate <xlsx>`：给外壳做输入预检，stdout 只输出一行 JSON。

    为什么并进引擎、而不是像以前那样单跑 `../engine/validate_input.py`：
    外壳原先按**编译期源码路径**找那个 .py、再用系统 `python` 启动它 ——
    路径是构建机的、用户机器也没有 Python，所以预检在安装版必然失败。
    本 exe 里已经打包了 openpyxl，由它来校验，开发版和安装版走同一条路径。
    """
    path = argv[0] if argv else ""
    try:
        from validate_input import validate
        payload = validate(path)
    except Exception as e:          # 预检出错不该让 exe 崩（--noconsole 会弹模态框卡住外壳）
        eerr(f"[engine] 输入预检失败 {type(e).__name__}: {e}")
        eerr(traceback.format_exc())
        payload = {"valid": False, "errors": [f"输入预检失败: {e}"],
                   "warnings": [], "stats": {}}
    try:
        _emit_json(payload)
    except Exception:
        pass
    return 0


def main() -> int:
    global _log_file, _start_time

    # 子命令分流必须放在最前面（含版本号之前）——stdout 只能是那一个 JSON，
    # 混进别的行外壳就解析不了。
    if len(sys.argv) >= 2 and sys.argv[1] == "--validate":
        return _run_validate(sys.argv[2:])

    _start_time = time.time()

    # 协议 §10：版本号打印到 stdout 第一行
    eout(f"排课引擎 v{ENGINE_VERSION}")

    if len(sys.argv) < 4:
        eerr("用法: engine <输入目录> <输出目录> <任务ID>")
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
        eerr(f"[engine] 输出目录不可写：{e}")
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
        plans, solve_warnings, status = solve_plans(bundle, log=elog)
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
                   "某班周课时超过可用格数、结构化约束（教师不可用/连堂禁日等）"
                   "与固定课或连堂设置冲突。请检查输入数据或放宽相应设置。")
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


def _emergency(e: BaseException) -> None:
    """最后一道兜底：绝不让异常逃出 main()。

    ⚠️ 打包成 --noconsole 的 exe 后，未捕获异常会让 PyInstaller 的窗口模式
    启动器弹一个模态框、进程停在那儿等人点 Close；而外壳正等着这个进程退出，
    用户看到的就是「排课卡住不动」。所以这里一律吞掉，并尽力留下 status.json。
    """
    eerr(f"[engine] 未捕获异常：{type(e).__name__}: {e}")
    eerr(traceback.format_exc())
    out_dir = sys.argv[2] if len(sys.argv) > 2 else ""
    task_id = sys.argv[3] if len(sys.argv) > 3 else "unknown"
    if not out_dir:
        return
    try:
        os.makedirs(out_dir, exist_ok=True)
        write_status(os.path.join(out_dir, "status.json"), task_id, 2,
                     "引擎内部出错，没能排出课表。请稍后重试；"
                     "若反复出现，请把输入的 Excel 发给技术支持。",
                     [], [])
    except Exception:
        pass


def _detach_streams() -> None:
    """退出前把 stdout / stderr 换成丢弃流。

    否则解释器收尾时会再 flush 一次，而管道可能已被调用方关闭，
    于是又抛一次 OSError（表现为进程退出码变成 120）。
    """
    for name in ("stdout", "stderr"):
        old = getattr(sys, name, None)
        try:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))
        except Exception:
            pass
        try:
            if old is not None:
                old.close()     # 就地关掉，别留到解释器收尾时再炸一次
        except Exception:
            pass


if __name__ == "__main__":
    rc = 0
    try:
        rc = main()
    except SystemExit as e:
        rc = e.code if isinstance(e.code, int) else 0
    except BaseException as e:      # noqa: BLE001 —— 兜底就是要抓全部
        _emergency(e)
    _detach_streams()
    sys.exit(rc)
