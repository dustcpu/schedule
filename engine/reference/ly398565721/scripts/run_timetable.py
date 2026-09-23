from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

from adapters.dehua import load_dehua_appointment
from adapters.xuefu import load_xuefu_appointment
from core.duty_solver import solve_duties
from core.formal_solver import solve_formal
from core.profile import RuleSetting, SchoolProfile
from core.validate_duty import validate_duties
from core.validate_formal import validate_formal


def profile_from_json(path: Path) -> SchoolProfile:
    raw = json.loads(path.read_text(encoding="utf-8"))
    rules = {rule_id: RuleSetting(**setting) for rule_id, setting in raw.items()}
    profile = SchoolProfile(rules)
    if profile.unanswered_confirmations():
        raise ValueError("规则确认表仍有未确认项，禁止求解")
    return profile


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="正式课→值班→四份Excel的受确认排课流程")
    parser.add_argument("--adapter", choices=("xuefu", "dehua"), required=True)
    parser.add_argument("--appointment", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True, help="已写入本次约束确认表的运行目录")
    parser.add_argument("--profile", type=Path, help="默认读取 --run-dir/rule_profile.json")
    parser.add_argument("--dehua-sheet")
    parser.add_argument("--node", default="node")
    parser.add_argument("--time-limit", type=int, default=120)
    args = parser.parse_args()

    profile_path = args.profile or args.run_dir / "rule_profile.json"
    profile = profile_from_json(profile_path)
    if not args.run_dir.is_dir():
        raise ValueError("运行目录不存在；请先生成并确认本次约束确认表")
    source_digest = digest(args.appointment)
    schedule_input = load_xuefu_appointment(args.appointment) if args.adapter == "xuefu" else load_dehua_appointment(args.appointment, sheet_name=args.dehua_sheet)

    formal = solve_formal(schedule_input, profile, time_limit_seconds=args.time_limit)
    write_json(args.run_dir / "formal_status.json", formal.to_dict())
    if formal.status == "INFEASIBLE_PENDING_RELAXATION":
        print("正式课因半天规则无解；已写出最小候选，等待用户批准，未继续值班或导出。")
        return 2
    if formal.status not in {"OPTIMAL", "FEASIBLE"}:
        print(f"正式课无解：{formal.status}；未继续值班或导出。")
        return 2
    formal_report = validate_formal(schedule_input, formal, profile)
    write_json(args.run_dir / "formal_validation.json", formal_report.to_dict())
    if not formal_report.ok:
        print("正式课独立校验失败；未继续值班或导出。")
        return 3
    write_json(args.run_dir / "solved_schedule.json", formal.to_dict())

    duties = solve_duties(schedule_input, formal, profile, time_limit_seconds=args.time_limit)
    write_json(args.run_dir / "duty_status.json", duties.to_dict())
    if duties.status not in {"OPTIMAL", "FEASIBLE"}:
        print(f"值班无解：{duties.status}；请确认需要放宽的约束，未导出。")
        return 2
    duty_report = validate_duties(schedule_input, formal, duties, profile)
    write_json(args.run_dir / "duty_validation.json", duty_report.to_dict())
    if not duty_report.ok:
        print("值班独立校验失败；未导出。")
        return 3
    write_json(args.run_dir / "solved_duty_roster.json", duties.to_dict())

    subprocess.run(
        [args.node, str(SKILL_ROOT / "export" / "export_workbooks.mjs"), str(args.run_dir / "solved_schedule.json"), str(args.run_dir / "solved_duty_roster.json"), str(args.run_dir / "outputs")],
        check=True,
        cwd=SKILL_ROOT,
    )
    if digest(args.appointment) != source_digest:
        raise RuntimeError("源聘任表摘要发生变化，已停止；请检查外部修改")
    print("求解、双重校验和四份Excel导出完成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
