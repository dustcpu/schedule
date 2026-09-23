from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_skill_exposes_required_user_facing_references_and_readme():
    assert (ROOT / "SKILL.md").is_file()
    assert (ROOT / "README.md").is_file()
    catalog = (ROOT / "references" / "constraint-catalog.md").read_text(encoding="utf-8")
    for rule_id in ("F01", "F20", "D01", "D18", "O04", "G05"):
        assert rule_id in catalog
    assert "O04 已删除" in catalog
    assert "每次必确认项" in catalog
