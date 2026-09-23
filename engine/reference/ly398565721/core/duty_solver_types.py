from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class DutyAssignment:
    day: str
    class_id: str
    duty_type: str
    teacher: str


@dataclass(frozen=True)
class SolvedDutyRoster:
    status: str
    assignments: tuple[DutyAssignment, ...] = ()
    relaxation_candidates: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {"status": self.status, "assignments": [asdict(item) for item in self.assignments], "relaxation_candidates": list(self.relaxation_candidates)}
