from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass

from core.duty_solver_types import DutyAssignment, SolvedDutyRoster
from core.models import ScheduleInput, SolvedSchedule
from core.profile import SchoolProfile


WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday")
EVENING_DAYS = ("Sunday", *WEEKDAYS)
EXCLUDED_SUBJECTS = frozenset({"体育", "信息技术", "音乐", "美术", "艺术（单美术/双音乐）"})


@dataclass(frozen=True)
class DutyValidationFailure:
    code: str
    detail: str


@dataclass(frozen=True)
class DutyValidationReport:
    ok: bool
    failures: tuple[DutyValidationFailure, ...]

    def to_dict(self) -> dict:
        return {"ok": self.ok, "failures": [asdict(item) for item in self.failures]}


def _settings(profile: SchoolProfile) -> tuple[dict, bool, str]:
    special = profile.rules["D13"].value or {}
    sunday_headteacher = bool(profile.rules["D14"].value)
    evening_mode = str(profile.rules["D15"].value or "one_to_three_prefer_two")
    return special, sunday_headteacher, evening_mode


def teaching_membership(schedule_input: ScheduleInput) -> dict[str, set[str]]:
    result: dict[str, set[str]] = defaultdict(set)
    for req in schedule_input.lessons:
        if req.subject not in EXCLUDED_SUBJECTS:
            result[req.class_id].add(req.teacher)
    return result


def validate_duties(schedule_input: ScheduleInput, formal: SolvedSchedule, roster: SolvedDutyRoster, profile: SchoolProfile) -> DutyValidationReport:
    """Independent audit of duty assignments; formal lessons are read-only input."""
    failures: list[DutyValidationFailure] = []
    seen: set[tuple[str, str]] = set()

    def fail(code: str, detail: str) -> None:
        if (code, detail) not in seen:
            seen.add((code, detail))
            failures.append(DutyValidationFailure(code, detail))

    special, sunday_headteacher, evening_mode = _settings(profile)
    class_type = getattr(schedule_input, "class_types", {})
    membership = teaching_membership(schedule_input)
    teacher_classes: dict[str, set[str]] = defaultdict(set)
    teacher_subjects: dict[tuple[str, str], set[str]] = defaultdict(set)
    for req in schedule_input.lessons:
        if req.subject not in EXCLUDED_SUBJECTS:
            teacher_classes[req.teacher].add(req.class_id)
            teacher_subjects[req.class_id, req.teacher].add(req.subject)
    formal_periods: dict[tuple[str, str, str], set[int]] = defaultdict(set)
    for lesson in formal.lessons:
        formal_periods[lesson.class_id, lesson.teacher, lesson.day].add(lesson.period)

    coverage = Counter((x.day, x.class_id, x.duty_type) for x in roster.assignments)
    expected = {(day, class_id, duty_type) for class_id in schedule_input.classes for day in WEEKDAYS for duty_type in ("early", "quiet", "evening")}
    expected |= {("Sunday", class_id, "evening") for class_id in schedule_input.classes}
    for key in expected:
        if coverage[key] != 1:
            fail("DUTY_COVERAGE", f"{key[0]} {key[1]} {key[2]}应1人，实际{coverage[key]}人")
    for key in coverage:
        if key not in expected:
            fail("UNEXPECTED_DUTY", f"不应存在值班：{key}")
    same_time = Counter((x.day, x.duty_type, x.teacher) for x in roster.assignments)
    for key, count in same_time.items():
        if count > 1:
            fail("DUTY_TIME_COLLISION", f"{key[2]}在{key[0]} {key[1]}值{count}个班")
    same_class_type = Counter((x.class_id, x.teacher, x.duty_type) for x in roster.assignments)
    for key, count in same_class_type.items():
        if count > 1:
            fail("SAME_CLASS_DUTY_REPEAT", f"{key[1]}在{key[0]}重复{key[2]}值班")

    counts = Counter((x.teacher, x.duty_type) for x in roster.assignments)
    for teacher, classes in teacher_classes.items():
        if len(classes) == 1:
            for duty_type, code in (("early", "ONE_CLASS_EARLY"), ("quiet", "ONE_CLASS_QUIET"), ("evening", "ONE_CLASS_EVENING")):
                if counts[teacher, duty_type] != 1:
                    fail(code, f"单班教师{teacher}{duty_type}应1次，实际{counts[teacher, duty_type]}次")
        else:
            for duty_type in ("early", "quiet"):
                if counts[teacher, duty_type] != 2:
                    fail("MULTI_CLASS_DAY_DUTY", f"多班教师{teacher}{duty_type}应2次，实际{counts[teacher, duty_type]}次")
            lower = 2 if evening_mode == "two_to_three" else 1
            if not lower <= counts[teacher, "evening"] <= 3:
                fail("MULTI_CLASS_EVENING", f"多班教师{teacher}晚自习应{lower}—3次，实际{counts[teacher, 'evening']}次")
        if counts[teacher, "early"] > 2:
            fail("EARLY_MAX", f"{teacher}早读{counts[teacher, 'early']}次")
        if counts[teacher, "quiet"] > 2:
            fail("QUIET_MAX", f"{teacher}静校{counts[teacher, 'quiet']}次")
        if counts[teacher, "evening"] == 3 and counts[teacher, "early"] + counts[teacher, "quiet"] > 3:
            fail("THREE_EVENING_DAYTIME_LIMIT", f"{teacher}三次晚自习但早读静校共{counts[teacher, 'early'] + counts[teacher, 'quiet']}次")

    evenings: dict[str, set[str]] = defaultdict(set)
    for item in roster.assignments:
        if item.teacher not in membership.get(item.class_id, set()):
            fail("DUTY_MEMBERSHIP", f"{item.teacher}不是{item.class_id}班学科任课教师")
        if item.duty_type == "early" and not (formal_periods[item.class_id, item.teacher, item.day] & {1, 2}):
            fail("EARLY_FORMAL_AVAILABILITY", f"{item.teacher}在{item.day}无第1或2节正式课")
        if item.duty_type == "quiet" and not (formal_periods[item.class_id, item.teacher, item.day] & {3, 4, 5, 6}):
            fail("QUIET_FORMAL_AVAILABILITY", f"{item.teacher}在{item.day}无第3—6节正式课")
        if item.duty_type == "evening":
            evenings[item.teacher].add(item.day)
            if item.day in WEEKDAYS and len(formal_periods[item.class_id, item.teacher, item.day] & {5, 6, 7, 8}) >= 3:
                fail("EVENING_LATE_LOAD", f"{item.teacher}在{item.day}第5—8节已有3节课")
            if sunday_headteacher and item.day == "Sunday" and schedule_input.homeroom_teachers.get(item.class_id) != item.teacher:
                fail("SUNDAY_HOMEROOM", f"{item.class_id}周日晚自习应为班主任")
            allowed = special.get(class_type.get(item.class_id, ""))
            if allowed and not (teacher_subjects[item.class_id, item.teacher] & set(allowed)):
                fail("SPECIAL_CLASS_EVENING", f"{item.teacher}不符合{item.class_id}特殊班型晚自习学科")
    for teacher, days in evenings.items():
        sequence = [day in days for day in EVENING_DAYS]
        if any(all(sequence[i:i + 3]) for i in range(len(sequence) - 2)):
            fail("THREE_CONSECUTIVE_EVENINGS", f"{teacher}连续三个晚上晚自习")
    return DutyValidationReport(not failures, tuple(failures))
