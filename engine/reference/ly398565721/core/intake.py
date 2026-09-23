from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from core.profile import SchoolProfile


class ConfirmationRequiredError(ValueError):
    pass


@dataclass(frozen=True)
class ConfirmationArtifacts:
    confirmation_markdown: Path
    rule_profile_json: Path


def write_confirmation_artifacts(profile: SchoolProfile, run_dir: Path) -> ConfirmationArtifacts:
    unanswered = profile.unanswered_confirmations()
    if unanswered:
        titles = "、".join(rule.title for rule in unanswered)
        raise ConfirmationRequiredError(f"尚未确认：{titles}")

    run_dir.mkdir(parents=True, exist_ok=False)
    confirmation_markdown = run_dir / "本次约束确认表.md"
    rule_profile_json = run_dir / "rule_profile.json"
    rows = [
        "# 本次约束确认表",
        "",
        "| 编号 | 规则 | 类型 | 本次取值 | 是否每次确认 |",
        "|---|---|---|---|---|",
    ]
    for setting in profile.rules.values():
        value = json.dumps(setting.value, ensure_ascii=False, sort_keys=True)
        rows.append(
            f"| {setting.id} | {setting.title} | {setting.kind} | {value} | {'是' if setting.confirmation_required else '否'} |"
        )
    rows.extend([
        "",
        "## 导出边界",
        "早读静校晚自习值班表固定列为：日期、班级、值班类型、教师姓名。",
        "O04 已删除：不作第3次晚自习人选偏好。",
    ])
    confirmation_markdown.write_text("\n".join(rows) + "\n", encoding="utf-8")
    rule_profile_json.write_text(
        json.dumps({rule_id: asdict(setting) for rule_id, setting in profile.rules.items()}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return ConfirmationArtifacts(confirmation_markdown, rule_profile_json)
