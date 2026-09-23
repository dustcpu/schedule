from pathlib import Path

import pytest
import subprocess
import sys

from core.intake import ConfirmationRequiredError, write_confirmation_artifacts
from core.profile import default_profile


CONFIRMATIONS = {
    "F04": {"days": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"], "periods": 8},
    "F05": [],
    "F17": {},
    "F18": {},
    "F19": [],
    "D01": "早读、静校周一至周五；晚自习周一至周五及周日",
    "D13": {"直升班": ["语文", "数学", "英语", "物理", "化学", "生物"]},
    "D14": False,
    "D15": "one_to_three_prefer_two",
    "D16": [],
}


def confirmed_profile():
    profile = default_profile()
    for rule_id, value in CONFIRMATIONS.items():
        profile = profile.with_confirmation(rule_id, value)
    return profile


def test_run_cannot_be_marked_ready_without_sunday_decision(tmp_path: Path):
    with pytest.raises(ConfirmationRequiredError, match="周日晚自习"):
        write_confirmation_artifacts(default_profile(), tmp_path)


def test_confirmation_artifact_keeps_deleted_o04_and_duty_roster_columns(tmp_path: Path):
    run_dir = tmp_path / "confirmed-run"
    result = write_confirmation_artifacts(confirmed_profile(), run_dir)
    text = result.confirmation_markdown.read_text(encoding="utf-8")
    assert "O04 已删除" in text
    assert "教师姓名" in text
    assert (run_dir / "rule_profile.json").is_file()


def test_create_run_cli_blocks_without_confirmation(tmp_path: Path):
    script = Path(__file__).resolve().parents[1] / "scripts" / "create_run.py"
    result = subprocess.run(
        [sys.executable, str(script), "--school", "测试学校", "--grade", "高一", "--runs-dir", str(tmp_path)],
        capture_output=True, text=True,
    )
    assert result.returncode == 2
    assert "尚未开始求解" in result.stdout
