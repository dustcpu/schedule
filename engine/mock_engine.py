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


def _plan_sheet(courses, period_labels):
    days = ["时间", "周一", "周二", "周三", "周四", "周五"]
    rows = [days]
    for ridx, lesson in enumerate(period_labels):
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
def write_pdf(path, task_id, plans, courses_by_plan, requirements, period_labels):
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
        table_data = [days]
        for ridx, lesson in enumerate(period_labels):
            row = [lesson] + list(courses_by_plan[i][ridx])
            table_data.append(row)

        tbl = Table(table_data, colWidths=[45*mm] + [22*mm]*5)
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

    # 读取硬限制预设条件
    hard_limits = {}
    hl_path = os.path.join(in_dir, "hard_limits.json")
    if os.path.exists(hl_path):
        with open(hl_path, "r", encoding="utf-8-sig") as f:
            hard_limits = json.load(f)

    sched = hard_limits.get("schedule", {})
    fixed_classes = hard_limits.get("fixed_classes", [])
    consec = hard_limits.get("consecutive", {})
    class_rule = hard_limits.get("classes", {})

    # 计算每节课时间段
    period_min = sched.get("period_minutes", 40)
    am_start = sched.get("morning_start", "08:00")
    am_end = sched.get("morning_end", "12:20")
    pm_start = sched.get("afternoon_start", "14:30")
    pm_end = sched.get("afternoon_end", "16:55")
    long_break_after = sched.get("long_break_after_period", 3)
    long_break_min = sched.get("long_break_minutes", 30)
    eye_break_after = sched.get("eye_break_after_period", 6)
    eye_break_min = sched.get("eye_break_minutes", 15)
    default_break = sched.get("default_break_minutes", 10)
    periods_per_day = sched.get("periods_per_day", 8)

    # 生成每节课时间段标签
    def parse_tm(t):
        h, m = t.split(":")
        return int(h)*60 + int(m)
    def fmt_tm(minutes):
        return f"{minutes//60:02d}:{minutes%60:02d}"

    # 按上午/下午分段生成节次时间：放不进上午的节跳到下午开始（午休不排课）
    period_labels = []
    cur = parse_tm(am_start)
    am_end_min = parse_tm(am_end)
    pm_start_min = parse_tm(pm_start)
    for p in range(1, periods_per_day+1):
        if p > 1 and cur < pm_start_min and cur + period_min > am_end_min:
            cur = pm_start_min
        start = cur
        end = cur + period_min
        period_labels.append(f"第{p}节 {fmt_tm(start)}-{fmt_tm(end)}")
        cur = end
        if p < periods_per_day:
            if p == long_break_after:
                cur += long_break_min
            elif p == eye_break_after:
                cur += eye_break_min
            else:
                cur += default_break

    time.sleep(1)

    # 根据硬限制生成课表（mock：体现固定课和连堂规则）
    days = ["周一", "周二", "周三", "周四", "周五"]
    # 8节 × 5天
    def empty_grid():
        return [["—"]*5 for _ in range(periods_per_day)]

    grids = [empty_grid(), empty_grid(), empty_grid()]

    # 应用固定课
    for fc in fixed_classes:
        day = fc.get("day", 1) - 1
        period = fc.get("period", 1) - 1
        subj = fc.get("subject", "")
        if 0 <= day < 5 and 0 <= period < periods_per_day:
            for g in grids:
                g[period][day] = subj

    # 应用连堂规则：周二上午数学连堂，周三语文，周四英语（上午第1-2节和4-5节）
    math_day = consec.get("math_day", 2) - 1
    chinese_day = consec.get("chinese_day", 3) - 1
    english_day = consec.get("english_day", 4) - 1
    # 上午连堂：第1-2节
    for g in grids:
        g[0][math_day] = "数学"
        g[1][math_day] = "数学"
        g[0][chinese_day] = "语文"
        g[1][chinese_day] = "语文"
        g[0][english_day] = "英语"
        g[1][english_day] = "英语"
        # 第4-5节另一个班也连堂
        g[3][math_day] = "数学"
        g[4][math_day] = "数学"
        g[3][chinese_day] = "语文"
        g[4][chinese_day] = "语文"
        g[3][english_day] = "英语"
        g[4][english_day] = "英语"

    # 填充其他科目（mock：简单填充）
    other_subjects = ["物理", "化学", "生物", "历史", "地理", "政治", "体育", "自习"]
    for g in grids:
        for p in range(periods_per_day):
            for d in range(5):
                if g[p][d] == "—":
                    g[p][d] = other_subjects[(p+d) % len(other_subjects)]

    courses_by_plan = grids

    plans = [
        {"index": 1, "name": "均衡方案", "score": 95.5,
         "note": "各班级课时均衡，教师负担均匀"},
        {"index": 2, "name": "紧凑方案", "score": 88.0,
         "note": "上课集中在周一至周四，周五空出"},
        {"index": 3, "name": "宽松方案", "score": 82.5,
         "note": "每天不超过6节课，自习时间充足"},
    ]

    # xlsx
    sheet_specs = [("总览", _overview_sheet(plans))]
    for i, p in enumerate(plans):
        sheet_specs.append((f"方案{p['index']}", _plan_sheet(courses_by_plan[i], period_labels)))
    write_xlsx(os.path.join(out_dir, "result.xlsx"), sheet_specs)

    # pdf（中文 + 表格）
    write_pdf(os.path.join(out_dir, "result.pdf"), task_id, plans, courses_by_plan, requirements, period_labels)

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
