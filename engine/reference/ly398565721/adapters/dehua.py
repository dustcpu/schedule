from __future__ import annotations

from pathlib import Path

from openpyxl import load_workbook

from core.models import LessonRequirement, ScheduleInput


DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday")
STANDARD_SUBJECTS = frozenset({"语文", "数学", "英语", "物理", "化学", "生物", "政治", "历史", "地理", "体育", "信息技术", "音乐", "美术"})


def load_dehua_appointment(workbook_path: Path, *, sheet_name: str | None = None) -> ScheduleInput:
    """Read a 德化-style wide appointment table headed 班级/学科/教师/周课时.

    The header row may appear above explanatory rows; adapters never write back to
    the source.  A campus-specific layout change is localized here, not in CP-SAT.
    """
    book = load_workbook(workbook_path, read_only=True, data_only=True)
    sheet = book[sheet_name] if sheet_name else book.active
    header_row = None
    headers: dict[str, int] = {}
    for row in range(1, min(sheet.max_row, 20) + 1):
        candidate = {str(sheet.cell(row, col).value).strip(): col for col in range(1, sheet.max_column + 1) if sheet.cell(row, col).value}
        if {"班级", "学科", "教师", "周课时"} <= set(candidate):
            header_row, headers = row, candidate
            break
    if header_row is None:
        raise ValueError("德化聘任表需要列标题：班级、学科、教师、周课时")
    lessons: list[LessonRequirement] = []
    for row in range(header_row + 1, sheet.max_row + 1):
        class_id = sheet.cell(row, headers["班级"]).value
        subject = sheet.cell(row, headers["学科"]).value
        teacher = sheet.cell(row, headers["教师"]).value
        hours = sheet.cell(row, headers["周课时"]).value
        if not any((class_id, subject, teacher, hours)):
            continue
        if not all((class_id, subject, teacher, hours)):
            raise ValueError(f"第{row}行聘任信息不完整")
        subject_text = str(subject).strip()
        if subject_text not in STANDARD_SUBJECTS:
            raise ValueError(f"第{row}行学科“{subject_text}”未被标准化，请先在适配器补映射")
        lessons.append(LessonRequirement(str(class_id).strip(), subject_text, str(teacher).strip(), int(hours)))
    if not lessons:
        raise ValueError("德化聘任表没有可读取的课程记录")
    classes = tuple(dict.fromkeys(item.class_id for item in lessons))
    return ScheduleInput(classes, DAYS, tuple(range(1, 9)), tuple(lessons))
