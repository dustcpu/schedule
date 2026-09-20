# -*- coding: utf-8 -*-
"""
排课助手 - 模拟引擎（mock_engine.py）
======================================
开发阶段占位引擎，用于让 UI 骨架全流程跑通（含多解输出 v1.1）。
真正的排课算法由团队算法同学用 Python 实现，打包成 engine.exe 后
放到 src-tauri/resources/engine/ 目录即可，UI 一行不用改。

用法（由外壳自动调用）：
    python mock_engine.py <输入目录> <输出目录> <任务ID>

输出约定：
    result.xlsx    多 sheet：总览 + 方案1/2/3
    result.pdf     多页：总览 + 每个方案一页（中文 + 表格）
    status.json    含 plans 数组
"""
import sys
import os
import json
import zipfile
import time
from datetime import datetime

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle


# 注册中文字体（系统自带微软雅黑）
def _register_font():
    candidates = [
        ("MSYH", "C:/Windows/Fonts/msyh.ttc"),
        ("SimSun", "C:/Windows/Fonts/simsun.ttc"),
    ]
    for name, path in candidates:
        if os.path.exists(path):
            try:
                pdfmetrics.registerFont(TTFont(name, path))
                return name
            except Exception:
                continue
    return "Helvetica"


# ---------------------------------------------------------------- xlsx 生成
def _sheet_xml(rows_data):
    rows_xml = []
    for ridx, row in enumerate(rows_data, start=1):
        cells = []
        for cidx, val in enumerate(row):
            col_letter = chr(ord('A') + cidx)
            cells.append(
                f'<c r="{col_letter}{ridx}" t="inlineStr"><is><t>{val}</t></is></c>'
            )
        rows_xml.append(f'<row r="{ridx}">{"".join(cells)}</row>')
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(rows_xml)}</sheetData>'
        '</worksheet>'
    )


def _overview_sheet(plans):
    rows = [["方案编号", "方案名", "评分", "特点"]]
    for p in plans:
        rows.append([str(p["index"]), p["name"], f'{p["score"]:.1f}', p["note"]])
    return _sheet_xml(rows)


def _plan_sheet(courses):
    days = ["时间", "周一", "周二", "周三", "周四", "周五"]
    lessons = [f"第{i}节" for i in range(1, 9)]
    rows = [days]
    for ridx, lesson in enumerate(lessons):
        row = [lesson]
        for c in courses[ridx]:
            row.append(c)
        rows.append(row)
    return _sheet_xml(rows)


def write_xlsx(path, sheet_specs):
    n = len(sheet_specs)
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        + ''.join(
            f'<Override PartName="/xl/worksheets/sheet{i}.xml" '
            f'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            for i in range(1, n + 1)
        )
        + '</Types>'
    )
    rels_root = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        '</Relationships>'
    )
    sheets_xml = "".join(
        f'<sheet name="{name}" sheetId="{i}" r:id="rId{i}"/>'
        for i, (name, _) in enumerate(sheet_specs, start=1)
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets>{sheets_xml}</sheets></workbook>'
    )
    rels_items = "".join(
        f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>'
        for i in range(1, n + 1)
    )
    workbook_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f'{rels_items}</Relationships>'
    )
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml', content_types)
        z.writestr('_rels/.rels', rels_root)
        z.writestr('xl/workbook.xml', workbook)
        z.writestr('xl/_rels/workbook.xml.rels', workbook_rels)
        for i, (_, xml) in enumerate(sheet_specs, start=1):
            z.writestr(f'xl/worksheets/sheet{i}.xml', xml)


# ---------------------------------------------------------------- pdf 生成
def write_pdf(path, task_id, plans, courses_by_plan, requirements):
    """生成中文 PDF：总览页 + 每个方案一页课表表格。"""
    font_name = _register_font()
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'TitleCN', parent=styles['Title'], fontName=font_name, fontSize=18, leading=24
    )
    h2_style = ParagraphStyle(
        'H2CN', parent=styles['Heading2'], fontName=font_name, fontSize=13, leading=18
    )
    body_style = ParagraphStyle(
        'BodyCN', parent=styles['Normal'], fontName=font_name, fontSize=10, leading=14
    )

    doc = SimpleDocTemplate(path, pagesize=A4,
                            leftMargin=18*mm, rightMargin=18*mm,
                            topMargin=20*mm, bottomMargin=20*mm)
    story = []

    # 总览页
    story.append(Paragraph("排课结果总览", title_style))
    story.append(Spacer(1, 8*mm))
    story.append(Paragraph(f"任务 ID：{task_id}", body_style))
    story.append(Paragraph(f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", body_style))
    story.append(Spacer(1, 6*mm))
    story.append(Paragraph(f"共生成 {len(plans)} 套方案：", h2_style))
    story.append(Spacer(1, 3*mm))

    overview_data = [["编号", "方案名", "评分", "特点"]]
    for p in plans:
        overview_data.append([str(p["index"]), p["name"], f'{p["score"]:.1f}', p["note"]])
    overview_tbl = Table(overview_data, colWidths=[20*mm, 35*mm, 20*mm, 90*mm])
    overview_tbl.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, -1), font_name),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2563eb')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
    ]))
    story.append(overview_tbl)

    if requirements:
        story.append(Spacer(1, 6*mm))
        story.append(Paragraph("用户特殊要求：", h2_style))
        for line in requirements.splitlines()[:5]:
            if line.strip():
                story.append(Paragraph(f"· {line}", body_style))

    # 每个方案一页
    for i, p in enumerate(plans):
        story.append(PageBreak())
        story.append(Paragraph(f"方案{p['index']}：{p['name']}", title_style))
        story.append(Spacer(1, 4*mm))
        story.append(Paragraph(f"评分：{p['score']:.1f}　　特点：{p['note']}", body_style))
        story.append(Spacer(1, 6*mm))

        days = ["时间", "周一", "周二", "周三", "周四", "周五"]
        lessons = [f"第{j}节" for j in range(1, 9)]
        table_data = [days]
        for ridx, lesson in enumerate(lessons):
            row = [lesson] + list(courses_by_plan[i][ridx])
            table_data.append(row)

        tbl = Table(table_data, colWidths=[20*mm] + [28*mm]*5)
        tbl.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (-1, -1), font_name),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#e5e7eb')),
            ('BACKGROUND', (0, 1), (0, -1), colors.HexColor('#f3f4f6')),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('TOPPADDING', (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ]))
        story.append(tbl)

    doc.build(story)


# ---------------------------------------------------------------- 主流程
def main():
    if len(sys.argv) < 4:
        print("用法: mock_engine.py <输入目录> <输出目录> <任务ID>", file=sys.stderr)
        sys.exit(1)

    in_dir = sys.argv[1]
    out_dir = sys.argv[2]
    task_id = sys.argv[3]
    os.makedirs(out_dir, exist_ok=True)

    input_xlsx = os.path.join(in_dir, "input.xlsx")
    if not os.path.exists(input_xlsx):
        for f in os.listdir(in_dir):
            if f.startswith("input."):
                input_xlsx = os.path.join(in_dir, f)
                break
        else:
            status = {"task_id": task_id, "code": 2,
                      "message": "输入目录里没有 input.xlsx",
                      "warnings": [], "plans": []}
            with open(os.path.join(out_dir, "status.json"), "w", encoding="utf-8") as f:
                json.dump(status, f, ensure_ascii=False, indent=2)
            sys.exit(0)

    requirements = ""
    req_path = os.path.join(in_dir, "requirements.txt")
    if os.path.exists(req_path):
        with open(req_path, "r", encoding="utf-8") as f:
            requirements = f.read().strip()

    time.sleep(1)

    plans = [
        {"index": 1, "name": "均衡方案", "score": 95.5,
         "note": "各班级课时均衡，教师负担均匀"},
        {"index": 2, "name": "紧凑方案", "score": 88.0,
         "note": "上课集中在周一至周四，周五空出"},
        {"index": 3, "name": "宽松方案", "score": 82.5,
         "note": "每天不超过6节课，自习时间充足"},
    ]

    courses_by_plan = [
        [["语文","数学","英语","物理","化学"]]*8,
        [["数学","语文","物理","英语","—"]]*4 + [["化学","自习","体育","阅览","—"]]*4,
        [["语文","数学","英语","—","—"]]*3 + [["物理","体育","自习","—","—"]]*3 + [["—","—","—","—","—"]]*2,
    ]

    # xlsx
    sheet_specs = [("总览", _overview_sheet(plans))]
    for i, p in enumerate(plans):
        sheet_specs.append((f"方案{p['index']}", _plan_sheet(courses_by_plan[i])))
    write_xlsx(os.path.join(out_dir, "result.xlsx"), sheet_specs)

    # pdf（中文 + 表格）
    write_pdf(os.path.join(out_dir, "result.pdf"), task_id, plans, courses_by_plan, requirements)

    warnings = [
        "【模拟引擎】这是占位输出，真正的排课结果请等待算法团队交付 engine.exe",
    ]
    if requirements:
        warnings.append("【模拟引擎】已收到你的文字要求，但未参与计算")

    status = {
        "task_id": task_id,
        "code": 1,
        "message": f"模拟排课完成，共生成 {len(plans)} 套方案（示例数据）",
        "plans": plans,
        "warnings": warnings,
    }
    with open(os.path.join(out_dir, "status.json"), "w", encoding="utf-8") as f:
        json.dump(status, f, ensure_ascii=False, indent=2)

    sys.exit(0)


if __name__ == "__main__":
    main()
