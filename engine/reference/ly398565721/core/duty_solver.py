from __future__ import annotations

from collections import defaultdict

from ortools.sat.python import cp_model

from core.duty_solver_types import DutyAssignment, SolvedDutyRoster
from core.models import ScheduleInput, SolvedSchedule
from core.profile import SchoolProfile
from core.validate_duty import EVENING_DAYS, EXCLUDED_SUBJECTS, WEEKDAYS, teaching_membership


def _formal_periods(formal: SolvedSchedule) -> dict[tuple[str, str, str], set[int]]:
    values: dict[tuple[str, str, str], set[int]] = defaultdict(set)
    for lesson in formal.lessons:
        values[lesson.class_id, lesson.teacher, lesson.day].add(lesson.period)
    return values


def solve_duties(schedule_input: ScheduleInput, formal: SolvedSchedule, profile: SchoolProfile, *, time_limit_seconds: int = 60) -> SolvedDutyRoster:
    """CP-SAT duty solve over frozen formal lessons; no formal variable is created."""
    special = profile.rules["D13"].value or {}
    sunday_headteacher = bool(profile.rules["D14"].value)
    evening_mode = str(profile.rules["D15"].value or "one_to_three_prefer_two")
    class_type = getattr(schedule_input, "class_types", {})
    membership = teaching_membership(schedule_input)
    teacher_classes: dict[str, set[str]] = defaultdict(set)
    teacher_subjects: dict[tuple[str, str], set[str]] = defaultdict(set)
    for req in schedule_input.lessons:
        if req.subject not in EXCLUDED_SUBJECTS:
            teacher_classes[req.teacher].add(req.class_id)
            teacher_subjects[req.class_id, req.teacher].add(req.subject)
    periods = _formal_periods(formal)
    duties = [(day, class_id, duty_type) for class_id in schedule_input.classes for day in WEEKDAYS for duty_type in ("early", "quiet", "evening")]
    duties += [("Sunday", class_id, "evening") for class_id in schedule_input.classes]
    model = cp_model.CpModel()
    x: dict[tuple[str, str, str, str], cp_model.IntVar] = {}
    for day, class_id, duty_type in duties:
        eligible = list(membership[class_id])
        for teacher in eligible:
            var = model.NewBoolVar(f"duty_{day}_{class_id}_{duty_type}_{teacher}")
            x[day, class_id, duty_type, teacher] = var
            if duty_type == "early" and not (periods[class_id, teacher, day] & {1, 2}):
                model.Add(var == 0)
            if duty_type == "quiet" and not (periods[class_id, teacher, day] & {3, 4, 5, 6}):
                model.Add(var == 0)
            if duty_type == "evening":
                if day in WEEKDAYS and len(periods[class_id, teacher, day] & {5, 6, 7, 8}) >= 3:
                    model.Add(var == 0)
                if sunday_headteacher and day == "Sunday" and schedule_input.homeroom_teachers.get(class_id) != teacher:
                    model.Add(var == 0)
                allowed = special.get(class_type.get(class_id, ""))
                if allowed and not (teacher_subjects[class_id, teacher] & set(allowed)):
                    model.Add(var == 0)
        model.Add(sum(x[day, class_id, duty_type, teacher] for teacher in eligible) == 1)
    for day in (*WEEKDAYS, "Sunday"):
        for duty_type in ("early", "quiet", "evening"):
            if day == "Sunday" and duty_type != "evening":
                continue
            for teacher in teacher_classes:
                group = [var for (candidate_day, _, candidate_type, candidate_teacher), var in x.items() if candidate_day == day and candidate_type == duty_type and candidate_teacher == teacher]
                if group:
                    model.Add(sum(group) <= 1)
    for class_id in schedule_input.classes:
        for teacher in membership[class_id]:
            for duty_type in ("early", "quiet", "evening"):
                group = [var for (_, candidate_class, candidate_type, candidate_teacher), var in x.items() if candidate_class == class_id and candidate_type == duty_type and candidate_teacher == teacher]
                if group:
                    model.Add(sum(group) <= 1)
    evening_counts = {}
    for teacher, classes in teacher_classes.items():
        for duty_type in ("early", "quiet", "evening"):
            group = [var for (_, _, candidate_type, candidate_teacher), var in x.items() if candidate_type == duty_type and candidate_teacher == teacher]
            if not group:
                continue
            if len(classes) == 1:
                model.Add(sum(group) == 1)
            elif duty_type in {"early", "quiet"}:
                model.Add(sum(group) == 2)
            elif duty_type == "evening":
                model.Add(sum(group) >= (2 if evening_mode == "two_to_three" else 1))
                model.Add(sum(group) <= 3)
                evening_counts[teacher] = sum(group)
            if duty_type == "early":
                model.Add(sum(group) <= 2)
            if duty_type == "quiet":
                model.Add(sum(group) <= 2)
        early = [var for (_, _, kind, candidate), var in x.items() if candidate == teacher and kind == "early"]
        quiet = [var for (_, _, kind, candidate), var in x.items() if candidate == teacher and kind == "quiet"]
        evening = [var for (_, _, kind, candidate), var in x.items() if candidate == teacher and kind == "evening"]
        if evening:
            third = model.NewBoolVar(f"third_evening_{teacher}")
            model.Add(sum(evening) == 3).OnlyEnforceIf(third)
            model.Add(sum(evening) <= 2).OnlyEnforceIf(third.Not())
            model.Add(sum(early) + sum(quiet) <= 3).OnlyEnforceIf(third)
            availability = []
            for day in EVENING_DAYS:
                day_vars = [var for (candidate_day, _, kind, candidate), var in x.items() if candidate_day == day and kind == "evening" and candidate == teacher]
                active = model.NewBoolVar(f"evening_{teacher}_{day}")
                if day_vars:
                    model.Add(sum(day_vars) == active)
                else:
                    model.Add(active == 0)
                availability.append(active)
            for index in range(len(availability) - 2):
                model.Add(sum(availability[index:index + 3]) <= 2)
    # Soft objectives: balance evening counts and avoid more than one type per day.
    penalties = []
    for teacher, count in evening_counts.items():
        delta = model.NewIntVar(0, 2, f"prefer_two_evenings_{teacher}")
        model.AddAbsEquality(delta, count - 2)
        penalties.append(delta)
    for teacher in teacher_classes:
        for day in WEEKDAYS:
            total = sum(var for (candidate_day, _, _, candidate), var in x.items() if candidate_day == day and candidate == teacher)
            extra = model.NewIntVar(0, 2, f"multi_duty_{teacher}_{day}")
            model.AddMaxEquality(extra, [total - 1, 0])
            penalties.append(extra)
    if penalties:
        model.Minimize(sum(penalties))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_seconds
    solver.parameters.num_search_workers = 8
    status = solver.StatusName(solver.Solve(model))
    if status not in {"OPTIMAL", "FEASIBLE"}:
        return SolvedDutyRoster(status)
    assignments = tuple(
        DutyAssignment(day, class_id, duty_type, teacher)
        for (day, class_id, duty_type, teacher), var in x.items() if solver.Value(var)
    )
    return SolvedDutyRoster(status, assignments)


__all__ = ["DutyAssignment", "SolvedDutyRoster", "solve_duties"]
