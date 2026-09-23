from core.duty_solver import DutyAssignment, SolvedDutyRoster, solve_duties
from core.models import LessonRequirement, ScheduleInput, ScheduledLesson, SolvedSchedule
from core.profile import default_profile
from core.validate_duty import validate_duties


def confirmed_profile():
    values = {
        "F04": {"days": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"], "periods": 8},
        "F05": [], "F17": {}, "F18": {}, "F19": [],
        "D01": "default", "D13": {}, "D14": False, "D15": "one_to_three_prefer_two", "D16": [],
    }
    profile = default_profile()
    for rule_id, value in values.items():
        profile = profile.with_confirmation(rule_id, value)
    return profile


def one_class_duty_input():
    return ScheduleInput(
        classes=("1",), days=("Monday", "Tuesday", "Wednesday", "Thursday", "Friday"), periods=tuple(range(1, 9)),
        lessons=(LessonRequirement("1", "语文", "A", 5),),
    )


def formal_every_day(period: int = 1):
    days = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday")
    return SolvedSchedule("FEASIBLE", tuple(ScheduledLesson("1", "语文", "A", day, period) for day in days))


def formal_with_early_and_quiet_availability():
    return SolvedSchedule("FEASIBLE", tuple(
        ScheduledLesson("1", "语文", "A", day, period)
        for day, period in (("Monday", 1), ("Tuesday", 3), ("Wednesday", 1), ("Thursday", 3), ("Friday", 1))
    ))


def test_one_class_teacher_gets_exactly_one_of_each_duty_type():
    roster = SolvedDutyRoster("FEASIBLE")
    report = validate_duties(one_class_duty_input(), formal_every_day(), roster, confirmed_profile())
    codes = {item.code for item in report.failures}
    assert {"ONE_CLASS_EARLY", "ONE_CLASS_QUIET", "ONE_CLASS_EVENING"} <= codes


def test_early_and_quiet_availability_use_confirmed_period_sets():
    roster = SolvedDutyRoster("FEASIBLE", (
        DutyAssignment("Monday", "1", "early", "A"),
        DutyAssignment("Monday", "1", "quiet", "A"),
    ))
    report = validate_duties(one_class_duty_input(), formal_every_day(period=7), roster, confirmed_profile())
    codes = {item.code for item in report.failures}
    assert {"EARLY_FORMAL_AVAILABILITY", "QUIET_FORMAL_AVAILABILITY"} <= codes


def test_solver_reports_infeasibility_instead_of_relaxing_one_class_duty_rules():
    formal = formal_with_early_and_quiet_availability()
    outcome = solve_duties(one_class_duty_input(), formal, confirmed_profile(), time_limit_seconds=3)
    assert outcome.status == "INFEASIBLE"
