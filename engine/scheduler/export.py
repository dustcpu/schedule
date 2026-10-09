# -*- coding: utf-8 -*-
"""导出层：写 result.xlsx / result.pdf / status.json（严格遵守接口协议 v1.1）。

result.xlsx：第一个 sheet 固定为「总览」，之后每个方案一个 sheet「方案N」。
result.pdf：第一页总览，之后每个方案一页（班级多则自动分页）。
status.json：UTF-8 无 BOM，code / message / plans / warnings。
"""
import os
import json
from typing import List, Dict, Any

from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, PatternFill, Border, Side

from reportlab.lib.pagesizes import A3, landscape
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

from . import __version__ as ENGINE_VERSION
from .config import DAYS, NUM_DAYS
from .data.load import NO_TEACHER_SUBJECTS
from .data.load import Problem
from .solver.solve import Plan

HEADER_FILL = "2563EB"
SUBHEADER_FILL = "E5E7EB"
SPORTS_ACTIVITY = "体育活动"  # 附加行显示名（不参与排课）
# 走班时段配色（2026-10-09）：与校方在用课表一致 —— 连排的第一节绿、第二节黄
WALK_FILL_FIRST = "C6EFCE"
WALK_FILL_SECOND = "FFEB9C"


# ---------------------------------------------------------------- 字体
def register_font() -> str:
    """注册中文字体，失败退回 Helvetica（PDF 不允许中文乱码，见协议 §4）。"""
    for name, path in (
        ("MSYH", "C:/Windows/Fonts/msyh.ttc"),
        ("SimSun", "C:/Windows/Fonts/simsun.ttc"),
        ("SimHei", "C:/Windows/Fonts/simhei.ttf"),
    ):
        try:
            if os.path.exists(path):
                pdfmetrics.registerFont(TTFont(name, path))
                return name
        except Exception:
            continue
    return "Helvetica"


# ---------------------------------------------------------------- Excel
def write_xlsx(path: str, plans: List[Plan], problem: Problem) -> None:
    cfg = problem.config
    wb = Workbook()

    # 总览 sheet（协议：第一个 sheet 固定为「总览」）
    ws = wb.active
    ws.title = "总览"
    thin = Side(style="thin", color="9CA3AF")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    headers = ["编号", "方案名", "评分", "特点"]
    for j, h in enumerate(headers, start=1):
        c = ws.cell(row=1, column=j, value=h)
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor=HEADER_FILL)
        c.alignment = Alignment(horizontal="center", vertical="center")
        c.border = border
    for i, p in enumerate(plans, start=2):
        vals = [p.index, p.name, p.score, p.note]
        for j, v in enumerate(vals, start=1):
            c = ws.cell(row=i, column=j, value=v)
            c.alignment = Alignment(horizontal="center" if j != 4 else "left",
                                    vertical="center")
            c.border = border
    ws.column_dimensions["A"].width = 8
    ws.column_dimensions["B"].width = 18
    ws.column_dimensions["C"].width = 8
    ws.column_dimensions["D"].width = 60

    # 每个方案一个 sheet：横向总表（行 = 天 × 节，列 = 每个行政班）
    # 2026-10-09 改版：原先是"每班一块、纵向堆在同一个 sheet 里"（25 块 × 13 行），
    # 现改为一张总表，便于整体查看，形态与校方在用课表一致。
    for p in plans:
        w = wb.create_sheet(f"方案{p.index}")
        _write_timetable_sheet(w, p, problem, border)

    # 每个方案一张任课表：课表网格自 2026-10-01 起为「学科\n教师」两行，
    # verify_hard.load_result_grids 已同步改为取第一行作学科，二者必须一起改；
    # 任课信息另出 sheet，便于按班级查看。
    for p in plans:
        _write_assign_sheet(wb, f"任课表{p.index}", p, problem, border)
        _write_walk_sheet(wb, f"走班表{p.index}", p, problem, border)

    wb.save(path)


def _timetable_rows(plan: Plan, problem: Problem):
    """横向总表的数据（含表头）—— Excel 与 PDF 共用，保证两种输出形态一致。

    返回 (rows, fills)：fills 与 rows 同形，值为该格的填充色（None = 不上色）。
    行 = 天 × 节（末尾可附加体育活动行），列 = 周 | 节 | 各班。形态对齐校方在用课表。
    """
    cfg = problem.config
    labels = cfg.period_labels()
    classes = problem.classes
    rows: List[List[str]] = [["周", "节"] + [ci.name or ci.id for ci in classes]]
    fills: List[List[Any]] = [[None] * len(rows[0])]
    for d in range(1, NUM_DAYS + 1):
        for k, per in enumerate(cfg.periods()):
            row = [DAYS[d - 1] if k == 0 else "", labels[per - 1]]
            fl: List[Any] = [None, None]
            for ci in classes:
                txt, fill = _cell_render(plan, ci.id, d, per, problem)
                row.append(txt)
                fl.append(fill)
            rows.append(row)
            fills.append(fl)
    if getattr(cfg.schedule, "sports_activity", True):
        rows.append(["", cfg.activity_label()] + [SPORTS_ACTIVITY] * len(classes))
        fills.append([None] * len(rows[0]))
    return rows, fills


def _write_timetable_sheet(w, plan: Plan, problem: Problem, border) -> None:
    """一张横向总表：行 = 天 × 节，列 = 每个行政班。

    2026-10-09 改版：原先是"每班一块、纵向堆在同一个 sheet 里"，
    现改为一张总表，便于整体查看与横向打印，形态与校方在用课表一致。
    格子只写学科；教师信息见「任课表N」。
    """
    rows, fills = _timetable_rows(plan, problem)
    for r, row in enumerate(rows, start=1):
        _write_row(w, r, row, border, header=(r == 1), fills=fills[r - 1])

    w.column_dimensions["A"].width = 6
    w.column_dimensions["B"].width = 20
    for j in range(3, len(rows[0]) + 1):
        w.column_dimensions[w.cell(row=1, column=j).column_letter].width = 12
    w.freeze_panes = "C2"     # 横向滚动时锁定「周 / 节」两列


def _write_row(w, r: int, cells: List[str], border, header: bool = False,
               fills: List[Any] = None) -> None:
    """写一行：统一加边框 / 居中 / 自动换行（走班格是多行文本，必须 wrap）。

    fills：与 cells 同形，非空处按该颜色填充（走班时段标记）。
    """
    multiline = any(isinstance(v, str) and "\n" in v for v in cells)
    for j, v in enumerate(cells, start=1):
        c = w.cell(row=r, column=j)
        if v:
            c.value = v
        c.border = border
        c.alignment = Alignment(horizontal="center", vertical="center",
                                wrap_text=multiline)
        if header:
            c.font = Font(bold=True)
            c.fill = PatternFill("solid", fgColor=SUBHEADER_FILL)
        elif fills and j - 1 < len(fills) and fills[j - 1]:
            c.fill = PatternFill("solid", fgColor=fills[j - 1])
    w.row_dimensions[r].height = 30 if multiline else 18


def _walk_here(plan: Plan, class_id: str, d: int, per: int, problem: Problem):
    """这一格该班是否在走班？是则返回 (独立时间ID, 起始节, 相对第几节)。"""
    if not getattr(problem, "walk_enabled", False):
        return None
    slots = {s.id: s for s in problem.walk_slots}
    for sid, starts in (getattr(plan, "walk_at", None) or {}).items():
        s = slots.get(sid)
        if not s:
            continue
        block = max(1, int(s.block))
        for sd, sp in starts:
            if sd == d and sp <= per <= sp + block - 1:
                return sid, sp, per - sp
    return None


def _walk_text(class_id: str, sid: str, problem: Problem) -> str:
    """走班格的显示文本：该班这一节分别去哪些教学班、上哪门课。"""
    lines = []
    for tc in problem.teaching_classes:
        if tc.slot != sid or class_id not in tc.source_classes:
            continue
        lines.append(f"{tc.subject} → {tc.classroom or '走班教室'}")
    return "\n".join(lines)


def _cell_render(plan: Plan, class_id: str, d: int, per: int, problem: Problem):
    """返回 (显示文本, 填充色)。填充色为 None 表示不上色。

    - 有常规课 → 学科
    - 走班格   → 多行「学科 → 去向」，并按时段第几节上色
    """
    subj = plan.grid.get((class_id, d, per), "")
    if subj:
        return subj, None
    w = _walk_here(plan, class_id, d, per, problem)
    if not w:
        return "", None
    sid, _sp, k = w
    return _walk_text(class_id, sid, problem), (WALK_FILL_FIRST if k == 0 else WALK_FILL_SECOND)


def _fmt_walk_time(plan: Plan, sid: str, problem: Problem) -> str:
    """把某个独立时间在该方案里的落位格式化成「周二 第7-8节；周四 第3-4节」。"""
    s = next((x for x in problem.walk_slots if x.id == sid), None)
    if not s:
        return ""
    block = max(1, int(s.block))
    parts = []
    for d, sp in (getattr(plan, "walk_at", None) or {}).get(sid, []):
        label = DAYS[d - 1] if 1 <= d <= NUM_DAYS else "?"
        parts.append(f"{label} 第{sp}-{sp + block - 1}节" if block > 1
                     else f"{label} 第{sp}节")
    return "；".join(parts)


def _write_walk_sheet(wb, sheet_name: str, plan: Plan, problem: Problem, border) -> None:
    """走班明细：教学班 / 独立时间 / 学科 / 来源 / 合计 / 教室 / 教师 / 时段。

    走班未启用时不建这张表（行为与之前完全一致）。
    """
    if not getattr(problem, "walk_enabled", False):
        return
    w = wb.create_sheet(sheet_name)
    header = ["教学班", "独立时间", "学科", "来源（班级　人数）",
              "合计人数", "教室", "任课教师", "本方案时段"]
    for j, v in enumerate(header, start=1):
        c = w.cell(row=1, column=j, value=v)
        c.font = Font(bold=True)
        c.fill = PatternFill("solid", fgColor=SUBHEADER_FILL)
        c.border = border
    r = 2
    for tc in problem.teaching_classes:
        src = "；".join(f"{(cid or '?')}　{n}" for cid, n in tc.sources)
        vals = [tc.id, tc.slot, tc.subject, src, tc.size or "",
                tc.classroom, problem.teacher_name(tc.teacher_id) or "（未指定）",
                _fmt_walk_time(plan, tc.slot, problem)]
        fill = WALK_FILL_FIRST
        for j, v in enumerate(vals, start=1):
            c = w.cell(row=r, column=j, value=v)
            c.border = border
            c.alignment = Alignment(horizontal="center", vertical="center",
                                    wrap_text=(j == 4))
            if j == 1:
                c.fill = PatternFill("solid", fgColor=fill)
        r += 1
    widths = [16, 10, 8, 30, 10, 14, 12, 22]
    for j, wd in enumerate(widths, start=1):
        w.column_dimensions[w.cell(row=1, column=j).column_letter].width = wd


def _write_assign_sheet(wb, sheet_name: str, plan: Plan, problem: Problem, border) -> None:
    """任课表：(班级 × 学科) → 教师姓名。

    重名教师写成「姓名(T012)」，便于校验器反查教师ID。
    """
    from collections import Counter

    # 需要教师的学科（按出现顺序，保持可读）
    subjects = []
    seen = set()
    for c in problem.courses:
        if c.subject in NO_TEACHER_SUBJECTS or c.subject in seen:
            continue
        seen.add(c.subject)
        subjects.append(c.subject)

    name_count = Counter(t.name for t in problem.teachers.values())

    def _cell_text(tid):
        if not tid:
            return "—"
        t = problem.teachers.get(tid)
        if not t:
            return tid
        return f"{t.name}({tid})" if name_count.get(t.name, 0) > 1 else t.name

    w = wb.create_sheet(sheet_name)
    header = ["班级ID", "班级名称"] + subjects
    for cj, val in enumerate(header, start=1):
        c = w.cell(row=1, column=cj, value=val)
        c.font = Font(bold=True)
        c.fill = PatternFill("solid", fgColor=SUBHEADER_FILL)
        c.border = border
    for ri, ci in enumerate(problem.classes, start=2):
        w.cell(row=ri, column=1, value=ci.id).border = border
        w.cell(row=ri, column=2, value=(ci.name or ci.id)).border = border
        for cj, s in enumerate(subjects, start=3):
            tid = plan.assign.get((ci.id, s))
            c = w.cell(row=ri, column=cj, value=_cell_text(tid))
            c.border = border
            c.alignment = Alignment(horizontal="center", vertical="center")
    w.column_dimensions["A"].width = 10
    w.column_dimensions["B"].width = 16
    for cj in range(3, 3 + len(subjects)):
        w.column_dimensions[w.cell(row=1, column=cj).column_letter].width = 12


# ---------------------------------------------------------------- PDF
def write_pdf(path: str, task_id: str, plans: List[Plan], problem: Problem,
              requirements: str) -> None:
    cfg = problem.config
    font = register_font()
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("T", parent=styles["Title"], fontName=font,
                                 fontSize=16, leading=22)
    body = ParagraphStyle("B", parent=styles["Normal"], fontName=font,
                          fontSize=9, leading=13)

    # A3 横向：25 个班横向排开，与校方在用课表的形态一致
    doc = SimpleDocTemplate(path, pagesize=landscape(A3),
                            leftMargin=12 * mm, rightMargin=12 * mm,
                            topMargin=12 * mm, bottomMargin=12 * mm)
    story: List[Any] = []

    # 第 1 页：总览
    story.append(Paragraph("排课结果总览", title_style))
    story.append(Spacer(1, 6 * mm))
    story.append(Paragraph(f"任务 ID：{task_id}", body))
    story.append(Paragraph(f"班级数：{len(problem.classes)}　　方案数：{len(plans)}", body))
    if requirements:
        story.append(Spacer(1, 3 * mm))
        story.append(Paragraph(f"特殊要求：{requirements[:60]}", body))
    story.append(Spacer(1, 5 * mm))

    data = [["编号", "方案名", "评分", "特点"]]
    for p in plans:
        data.append([str(p.index), p.name, f"{p.score:.1f}", p.note])
    t = Table(data, colWidths=[16 * mm, 32 * mm, 16 * mm, 104 * mm])
    t.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), font),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#" + HEADER_FILL)),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (2, -1), "CENTER"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(t)

    # 每方案一页：横向总表（行 = 天 × 节，列 = 每个行政班）
    for p in plans:
        story.append(PageBreak())
        story.append(Paragraph(f"方案{p.index}：{p.name}　评分 {p.score:.1f}", title_style))
        story.append(Spacer(1, 3 * mm))
        rows, fills = _timetable_rows(p, problem)
        cell_style = ParagraphStyle("cell", fontName=font, fontSize=6.5, leading=8,
                                    alignment=1)  # TA_CENTER
        # 走班格可能是多行文本；reportlab 的纯字符串不解析换行，转 Paragraph 才能换行
        rendered = [
            [Paragraph(str(v).replace("\n", "<br/>"), cell_style)
             if isinstance(v, str) and "\n" in v else str(v)
             for v in row]
            for row in rows]
        n_cls = len(problem.classes)
        avail = 420 * mm - 24 * mm          # A3 横向减去左右边距
        first_two = 6 * mm + 22 * mm        # 「周」「节」两列
        per_cls = max((avail - first_two) / max(n_cls, 1), 8 * mm)
        tbl = Table(rendered, colWidths=[6 * mm, 22 * mm] + [per_cls] * n_cls,
                    repeatRows=1)
        cmds = [
            ("FONTNAME", (0, 0), (-1, -1), font),
            ("FONTSIZE", (0, 0), (-1, -1), 6.5),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#" + SUBHEADER_FILL)),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 1),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
        ]
        # 走班时段按格上色（与 Excel 一致：连排第一节绿、第二节黄）
        for ri, fl in enumerate(fills):
            if ri >= len(rendered):
                break
            for ci_, fill in enumerate(fl):
                if fill:
                    cmds.append(("BACKGROUND", (ci_, ri), (ci_, ri),
                                 colors.HexColor("#" + fill)))
        tbl.setStyle(TableStyle(cmds))
        story.append(tbl)

    doc.build(story)


# ---------------------------------------------------------------- status.json
def write_status(path: str, task_id: str, code: int, message: str,
                 plans: List[Plan], warnings: List[str]) -> None:
    payload = {
        "task_id": task_id,
        "code": code,
        "message": message,
        # 打包用了 --noconsole（协议 §2），stdout 版本号在发布形态不可见，
        # 故在此冗余一份，便于外壳与用户追溯引擎版本。
        "engine_version": ENGINE_VERSION,
        "plans": [
            {"index": p.index, "name": p.name, "score": p.score, "note": p.note}
            for p in plans
        ],
        "warnings": warnings,
    }
    # UTF-8 无 BOM（协议 §7）
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
