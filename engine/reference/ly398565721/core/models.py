from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Mapping


Slot = tuple[str, int]


@dataclass(frozen=True)
class LessonRequirement:
    class_id: str
    subject: str
    teacher: str
    weekly_hours: int
    resource: str | None = None
    class_type: str | None = None


@dataclass(frozen=True)
class ScheduleInput:
    """Campus-neutral data required for formal-course scheduling."""

    classes: tuple[str, ...]
    days: tuple[str, ...]
    periods: tuple[int, ...]
    lessons: tuple[LessonRequirement, ...]
    fixed_slots: Mapping[tuple[str, str], frozenset[Slot]] = field(default_factory=dict)
    blocked_slots: Mapping[str, frozenset[Slot]] = field(default_factory=dict)
    subject_blocked_slots: Mapping[str, frozenset[Slot]] = field(default_factory=dict)
    teacher_blocked_slots: Mapping[str, frozenset[Slot]] = field(default_factory=dict)
    teacher_roles: Mapping[str, frozenset[str]] = field(default_factory=dict)
    homeroom_teachers: Mapping[str, str] = field(default_factory=dict)

    @property
    def slots(self) -> tuple[Slot, ...]:
        return tuple((day, period) for day in self.days for period in self.periods)

    @property
    def weekly_hours(self) -> dict[tuple[str, str], int]:
        return {(lesson.class_id, lesson.subject): lesson.weekly_hours for lesson in self.lessons}


@dataclass(frozen=True)
class ScheduledLesson:
    class_id: str
    subject: str
    teacher: str
    day: str
    period: int
    resource: str | None = None


@dataclass(frozen=True)
class SolvedSchedule:
    status: str
    lessons: tuple[ScheduledLesson, ...] = ()
    relaxation_candidates: tuple[tuple[str, str], ...] = ()

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "lessons": [asdict(lesson) for lesson in self.lessons],
            "relaxation_candidates": [list(item) for item in self.relaxation_candidates],
        }
