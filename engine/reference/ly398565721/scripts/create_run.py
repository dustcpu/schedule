from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

from core.intake import ConfirmationRequiredError, write_confirmation_artifacts
from core.profile import default_profile


def main() -> int:
    parser = argparse.ArgumentParser(description="创建排课运行目录并生成确认表")
    parser.add_argument("--school", required=True)
    parser.add_argument("--grade", required=True)
    parser.add_argument("--runs-dir", type=Path, required=True)
    args = parser.parse_args()
    run_dir = args.runs_dir / f"{datetime.now():%Y%m%d}-{args.school}-{args.grade}"
    try:
        artifacts = write_confirmation_artifacts(default_profile(), run_dir)
    except ConfirmationRequiredError as error:
        print(f"尚未开始求解：{error}")
        return 2
    print(artifacts.confirmation_markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
