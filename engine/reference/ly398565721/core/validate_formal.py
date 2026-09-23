from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass

from core.models import ScheduleInput, SolvedSchedule
from core.profile import SchoolProfile


MORNING = frozenset({1, 2, 3, 4})
AFTERNOON = frozenset({5, 6, 7, 8})
DOUBLE_PAIRS = frozenset({(1, 2), (2, 3), (3, 4), (5, 6), (6, 7), (7, 8)})


@dataclass(frozen=True)
class ValidationFailure:
    code: str
    detail: str


@dataclass(frozen=True)
class ValidationReport:
    ok: bool
    failures: tuple[ValidationFailure, ...]

    def to_dict(self) -> dict:
        return {"ok": self.ok, "failures": [asdict(item) for item in self.failures]}


def validate_formal(schedule_input: ScheduleInput, solved: SolvedSchedule, profile: SchoolProfile) -> ValidationReport:
    """Recheck materialized lessons without referring to CP-SAT variables."""
    del profile
    failures: list[ValidationFailure] = []
    seen: set[tuple[str, str]] = set()

    def fail(code: str, detail: str) -> None:
        if (code, detail) not in seen:
            seen.add((code, detail))
            failures.append(ValidationFailure(code, detail))

    expected = schedule_input.weekly_hours
    actual = Counter((x.class_id, x.subject) for x in solved.lessons)
    for key, hours in expected.items():
        if actual[key] != hours:
            fail("WEEKLY_HOURS", f"{key[0]} {key[1]} 需要{hours}节，实际{actual[key]}节")
    for key in actual:
        if key not in expected:
            fail("UNKNOWN_LESSON", f"出现聘任表外课程：{key[0]} {key[1]}")

    by_class_slot = Counter((x.class_id, x.day, x.period) for x in solved.lessons)
    by_teacher_slot = Counter((x.teacher, x.day, x.period) for x in solved.lessons)
    by_resource_slot = Counter((x.resource, x.day, x.period) for x in solved.lessons if x.resource)
    for key, count in by_class_slot.items():
        if count > 1:
            fail("CLASS_SLOT_COLLISION", f"{key[0]}在{key[1]}第{key[2]}节有{count}门课")
    for key, count in by_teacher_slot.items():
        if count > 1:
            fail("TEACHER_SLOT_COLLISION", f"{key[0]}在{key[1]}第{key[2]}节教{count}个班")
    for key, count in by_resource_slot.items():
        if count > 1:
            fail("RESOURCE_SLOT_COLLISION", f"资源{key[0]}在{key[1]}第{key[2]}节冲突")

    requirement = {(x.class_id, x.subject): x for x in schedule_input.lessons}
    teacher_days: dict[tuple[str, str], list[ScheduledLesson]] = defaultdict(list)
    subject_days: dict[tuple[str, str, str], list[int]] = defaultdict(list)
    math_days: dict[tuple[str, str], list[ScheduledLesson]] = defaultdict(list)
    for item in solved.lessons:
        req = requirement.get((item.class_id, item.subject))
        if item.day not in schedule_input.days or item.period not in schedule_input.periods:
            fail("INVALID_SLOT", f"{item.class_id} {item.subject}时段无效")
            continue
        if req is None:
            continue
        if item.teacher != req.teacher:
            fail("TEACHER_MISMATCH", f"{item.class_id} {item.subject}应为{req.teacher}，实际{item.teacher}")
        slot = (item.day, item.period)
        if slot in schedule_input.blocked_slots.get(item.class_id, frozenset()):
            fail("FIXED_SLOT", f"{item.class_id}占用禁排时段{item.day}第{item.period}节")
        fixed = schedule_input.fixed_slots.get((item.class_id, item.subject))
        if fixed and slot not in fixed:
            fail("FIXED_SLOT", f"{item.class_id} {item.subject}未排在指定时段")
        if slot in schedule_input.subject_blocked_slots.get(item.subject, frozenset()):
            fail("SUBJECT_UNAVAILABLE", f"{item.subject}排在禁排时段{item.day}第{item.period}节")
        if slot in schedule_input.teacher_blocked_slots.get(item.teacher, frozenset()):
            fail("TEACHER_UNAVAILABLE", f"{item.teacher}排在禁排时段{item.day}第{item.period}节")
        if item.subject in {"体育", "信息技术", "音乐", "美术"} and item.period in {1, 2}:
            fail("ACTIVITY_EARLY_PERIOD", f"{item.subject}排在第{item.period}节")
        teacher_days[(item.teacher, item.day)].append(item)
        subject_days[(item.class_id, item.subject, item.day)].append(item.period)
        if item.subject == "数学":
            math_days[(item.teacher, item.day)].append(item)

    by_teacher_requirements: dict[str, list] = defaultdict(list)
    for req in schedule_input.lessons:
        by_teacher_requirements[req.teacher].append(req)
    for (teacher, day), items in teacher_days.items():
        assigned_classes = {req.class_id for req in by_teacher_requirements[teacher]}
        weekly_total = sum(x.weekly_hours for x in by_teacher_requirements[teacher])
        if weekly_total <= 12:
            if len(assigned_classes) == 1 and len(items) > 2:
                fail("ONE_CLASS_DAILY_LIMIT", f"{teacher}在{day}有{len(items)}节")
            if len(assigned_classes) == 2:
                if len(items) > 3:
                    fail("TWO_CLASS_DAILY_LIMIT", f"{teacher}在{day}有{len(items)}节")
                by_class = Counter(x.class_id for x in items)
                if any(count >= 3 for count in by_class.values()):
                    fail("TWO_CLASS_SAME_CLASS_THREE", f"{teacher}在{day}同一班有3节")
        periods = {item.period for item in items}
        if periods & MORNING and periods & AFTERNOON:
            fail("HALF_DAY", f"{teacher}在{day}跨上午和下午上课")

    for (teacher, day), items in math_days.items():
        if len(items) > 3:
            fail("MATH_DAILY_LIMIT", f"{teacher}在{day}有{len(items)}节数学")
        doubles = Counter(x.class_id for x in items)
        if sum(1 for number in doubles.values() if number >= 2) >= 2:
            fail("MATH_TWO_CLASS_DOUBLE", f"{teacher}在{day}有两个班各上两节数学")

    for (class_id, subject), hours in expected.items():
        day_periods = [sorted(subject_days[(class_id, subject, day)]) for day in schedule_input.days]
        if hours < 5 and any(len(x) > 1 for x in day_periods):
            fail("DAILY_DISTRIBUTION", f"{class_id} {subject}周课时少于5却同日重复")
        if hours in {5, 6, 7} and any(len(x) == 0 for x in day_periods):
            fail("DAILY_DISTRIBUTION", f"{class_id} {subject}周课时{hours}却未每天出现")
        needed_doubles = 1 if hours == 6 else 2 if hours == 7 else 0
        if needed_doubles:
            actual_doubles = [x for x in day_periods if len(x) == 2 and tuple(x) in DOUBLE_PAIRS]
            if len(actual_doubles) != needed_doubles or any(len(x) not in {1, 2} for x in day_periods):
                fail("DOUBLE_LESSON", f"{class_id} {subject}连堂数量或位置不符合周课时{hours}")
        for day, periods in zip(schedule_input.days, day_periods, strict=True):
            if subject == "体育" and len(periods) > 1:
                fail("SPORT_DAILY_LIMIT", f"{class_id}在{day}有{len(periods)}节体育")
    return ValidationReport(not failures, tuple(failures))
