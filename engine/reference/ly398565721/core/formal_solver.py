from __future__ import annotations

from collections import defaultdict

from ortools.sat.python import cp_model

from core.models import ScheduleInput, ScheduledLesson, SolvedSchedule
from core.profile import SchoolProfile
from core.validate_formal import AFTERNOON, DOUBLE_PAIRS, MORNING


def _is_forbidden(schedule_input: ScheduleInput, req, day: str, period: int) -> bool:
    slot = (day, period)
    fixed = schedule_input.fixed_slots.get((req.class_id, req.subject))
    return bool(
        (fixed and slot not in fixed)
        or slot in schedule_input.blocked_slots.get(req.class_id, frozenset())
        or slot in schedule_input.subject_blocked_slots.get(req.subject, frozenset())
        or slot in schedule_input.teacher_blocked_slots.get(req.teacher, frozenset())
        or (req.subject in {"体育", "信息技术", "音乐", "美术"} and period in {1, 2})
    )


def _build_model(schedule_input: ScheduleInput, *, enforce_half_day: bool):
    model = cp_model.CpModel()
    x: dict[tuple[int, str, int], cp_model.IntVar] = {}
    for index, req in enumerate(schedule_input.lessons):
        for day, period in schedule_input.slots:
            var = model.NewBoolVar(f"L{index}_{day}_{period}")
            x[index, day, period] = var
            if _is_forbidden(schedule_input, req, day, period):
                model.Add(var == 0)
        model.Add(sum(x[index, day, period] for day, period in schedule_input.slots) == req.weekly_hours)

    for class_id in schedule_input.classes:
        for day, period in schedule_input.slots:
            model.Add(sum(x[index, day, period] for index, req in enumerate(schedule_input.lessons) if req.class_id == class_id) <= 1)
    for label, predicate in (
        ("teacher", lambda req: req.teacher),
        ("resource", lambda req: req.resource),
    ):
        groups: dict[str, list[int]] = defaultdict(list)
        for index, req in enumerate(schedule_input.lessons):
            key = predicate(req)
            if key:
                groups[key].append(index)
        for indexes in groups.values():
            for day, period in schedule_input.slots:
                model.Add(sum(x[index, day, period] for index in indexes) <= 1)

    for index, req in enumerate(schedule_input.lessons):
        for day in schedule_input.days:
            day_vars = [x[index, day, period] for period in schedule_input.periods]
            if req.weekly_hours < 5:
                model.Add(sum(day_vars) <= 1)
            elif req.weekly_hours in {5, 6, 7}:
                model.Add(sum(day_vars) >= 1)
            if req.weekly_hours in {6, 7}:
                double = model.NewBoolVar(f"double_{index}_{day}")
                model.Add(sum(day_vars) == 1 + double)
                pair_vars = []
                for first, second in DOUBLE_PAIRS:
                    pair = model.NewBoolVar(f"pair_{index}_{day}_{first}")
                    model.Add(pair <= x[index, day, first])
                    model.Add(pair <= x[index, day, second])
                    model.Add(pair >= x[index, day, first] + x[index, day, second] - 1)
                    pair_vars.append(pair)
                model.Add(sum(pair_vars) >= double)
        if req.weekly_hours in {6, 7}:
            model.Add(sum(x[index, day, period] for day in schedule_input.days for period in schedule_input.periods) == req.weekly_hours)
            double_days = []
            for day in schedule_input.days:
                indicator = model.NewBoolVar(f"double_day_{index}_{day}")
                model.Add(sum(x[index, day, period] for period in schedule_input.periods) == 1 + indicator)
                double_days.append(indicator)
            model.Add(sum(double_days) == (1 if req.weekly_hours == 6 else 2))

    by_teacher: dict[str, list[int]] = defaultdict(list)
    for index, req in enumerate(schedule_input.lessons):
        by_teacher[req.teacher].append(index)
    half_day_violations: list[tuple[str, str, cp_model.IntVar]] = []
    for teacher, indexes in by_teacher.items():
        total = sum(schedule_input.lessons[index].weekly_hours for index in indexes)
        taught_classes = {schedule_input.lessons[index].class_id for index in indexes}
        for day in schedule_input.days:
            day_vars = [x[index, day, period] for index in indexes for period in schedule_input.periods]
            if total <= 12:
                if len(taught_classes) == 1:
                    model.Add(sum(day_vars) <= 2)
                elif len(taught_classes) == 2:
                    model.Add(sum(day_vars) <= 3)
                    for class_id in taught_classes:
                        model.Add(sum(x[index, day, period] for index in indexes if schedule_input.lessons[index].class_id == class_id for period in schedule_input.periods) <= 2)
            morning = sum(x[index, day, period] for index in indexes for period in schedule_input.periods if period in MORNING)
            afternoon = sum(x[index, day, period] for index in indexes for period in schedule_input.periods if period in AFTERNOON)
            if enforce_half_day:
                half = model.NewBoolVar(f"half_{teacher}_{day}")
                model.Add(morning <= len(indexes) * len(schedule_input.periods) * half)
                model.Add(afternoon <= len(indexes) * len(schedule_input.periods) * (1 - half))
            else:
                violation = model.NewBoolVar(f"half_exception_{teacher}_{day}")
                half = model.NewBoolVar(f"diagnostic_half_{teacher}_{day}")
                model.Add(afternoon == 0).OnlyEnforceIf([violation.Not(), half])
                model.Add(morning == 0).OnlyEnforceIf([violation.Not(), half.Not()])
                half_day_violations.append((teacher, day, violation))
    for teacher, indexes in by_teacher.items():
        for day in schedule_input.days:
            math_vars = [x[index, day, period] for index in indexes if schedule_input.lessons[index].subject == "数学" for period in schedule_input.periods]
            if math_vars:
                model.Add(sum(math_vars) <= 3)
                double_classes = []
                for class_id in {schedule_input.lessons[index].class_id for index in indexes}:
                    value = sum(x[index, day, period] for index in indexes if schedule_input.lessons[index].class_id == class_id and schedule_input.lessons[index].subject == "数学" for period in schedule_input.periods)
                    flag = model.NewBoolVar(f"math_double_{teacher}_{class_id}_{day}")
                    model.Add(value >= 2).OnlyEnforceIf(flag)
                    model.Add(value <= 1).OnlyEnforceIf(flag.Not())
                    double_classes.append(flag)
                model.Add(sum(double_classes) <= 1)
    for class_id in schedule_input.classes:
        for day in schedule_input.days:
            model.Add(sum(x[index, day, period] for index, req in enumerate(schedule_input.lessons) if req.class_id == class_id and req.subject == "体育" for period in schedule_input.periods) <= 1)
    return model, x, half_day_violations


def _materialize(schedule_input: ScheduleInput, solver: cp_model.CpSolver, x) -> tuple[ScheduledLesson, ...]:
    materialized = []
    for index, req in enumerate(schedule_input.lessons):
        for day, period in schedule_input.slots:
            if solver.Value(x[index, day, period]):
                materialized.append(ScheduledLesson(req.class_id, req.subject, req.teacher, day, period, req.resource))
    return tuple(materialized)


def solve_formal(schedule_input: ScheduleInput, profile: SchoolProfile, *, time_limit_seconds: int = 60) -> SolvedSchedule:
    """Solve formal lessons; offer minimum F07 exceptions but never accept them."""
    del profile
    model, x, _ = _build_model(schedule_input, enforce_half_day=True)
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_seconds
    solver.parameters.num_search_workers = 8
    status = solver.StatusName(solver.Solve(model))
    if status in {"OPTIMAL", "FEASIBLE"}:
        return SolvedSchedule(status=status, lessons=_materialize(schedule_input, solver, x))
    diagnostic, diagnostic_x, violations = _build_model(schedule_input, enforce_half_day=False)
    diagnostic.Minimize(sum(flag for _, _, flag in violations))
    diagnostic_solver = cp_model.CpSolver()
    diagnostic_solver.parameters.max_time_in_seconds = time_limit_seconds
    diagnostic_solver.parameters.num_search_workers = 8
    diagnostic_status = diagnostic_solver.StatusName(diagnostic_solver.Solve(diagnostic))
    if diagnostic_status not in {"OPTIMAL", "FEASIBLE"}:
        return SolvedSchedule(status=status)
    candidates = tuple((teacher, day) for teacher, day, flag in violations if diagnostic_solver.Value(flag))
    return SolvedSchedule(status="INFEASIBLE_PENDING_RELAXATION", relaxation_candidates=candidates)
