# -*- coding: utf-8 -*-
"""输入文件校验：检查 input.xlsx 的结构和数据完整性。
用法: python validate_input.py <文件路径>
输出: JSON 到 stdout，格式 {"valid": bool, "errors": [...], "warnings": [...], "stats": {...}}
"""
import json
import sys
import os

def validate(path):
    errors = []
    warnings = []
    stats = {}

    if not os.path.exists(path):
        return {"valid": False, "errors": ["文件不存在"], "warnings": [], "stats": {}}

    if not path.lower().endswith(('.xlsx', '.xlsm')):
        errors.append("文件格式不是 .xlsx")

    try:
        from openpyxl import load_workbook
    except ImportError:
        return {"valid": False, "errors": ["缺少 openpyxl 库"], "warnings": [], "stats": {}}

    try:
        wb = load_workbook(path, data_only=True, read_only=True)
    except Exception as e:
        return {"valid": False, "errors": [f"无法打开文件: {e}"], "warnings": [], "stats": {}}

    sheet_names = wb.sheetnames

    # 检查三个必要 sheet
    required_sheets = {
        "教师": ["教师ID", "任教学科", "教师姓名"],
        "班级": ["班级ID", "班级名称", "年级"],
        "课程": ["班级ID", "学科", "周课时"],
    }

    sheets_found = {}
    for name, cols in required_sheets.items():
        found = None
        for sn in sheet_names:
            if sn.strip() == name:
                found = sn
                break
        if found is None:
            errors.append(f"缺少「{name}」工作表")
        else:
            sheets_found[name] = found

    if errors:
        wb.close()
        return {"valid": False, "errors": errors, "warnings": warnings, "stats": stats}

    # 读取教师表
    teachers = {}
    ws = wb[sheets_found["教师"]]
    rows = list(ws.iter_rows(values_only=True))
    if len(rows) < 2:
        errors.append("「教师」表没有数据行")
    else:
        headers = [str(h).strip() if h else "" for h in rows[0]]
        for col in required_sheets["教师"]:
            if col not in headers:
                errors.append(f"「教师」表缺少列：{col}")
        if not errors:
            tid_idx = headers.index("教师ID")
            subj_idx = headers.index("任教学科")
            name_idx = headers.index("教师姓名")
            for r in rows[1:]:
                if r is None or all(c is None for c in r):
                    continue
                tid = str(r[tid_idx]).strip() if r[tid_idx] else ""
                if not tid:
                    continue
                teachers[tid] = {
                    "name": str(r[name_idx]).strip() if r[name_idx] else tid,
                    "subject": str(r[subj_idx]).strip() if r[subj_idx] else "",
                }
            stats["teachers"] = len(teachers)
            if len(teachers) == 0:
                errors.append("「教师」表没有有效教师数据")

    # 读取班级表
    classes = {}
    ws = wb[sheets_found["班级"]]
    rows = list(ws.iter_rows(values_only=True))
    if len(rows) < 2:
        errors.append("「班级」表没有数据行")
    else:
        headers = [str(h).strip() if h else "" for h in rows[0]]
        for col in required_sheets["班级"]:
            if col not in headers:
                errors.append(f"「班级」表缺少列：{col}")
        if not errors:
            cid_idx = headers.index("班级ID")
            name_idx = headers.index("班级名称")
            grade_idx = headers.index("年级")
            for r in rows[1:]:
                if r is None or all(c is None for c in r):
                    continue
                cid = str(r[cid_idx]).strip() if r[cid_idx] else ""
                if not cid:
                    continue
                classes[cid] = {
                    "name": str(r[name_idx]).strip() if r[name_idx] else cid,
                    "grade": str(r[grade_idx]).strip() if r[grade_idx] else "",
                }
            stats["classes"] = len(classes)
            if len(classes) == 0:
                errors.append("「班级」表没有有效班级数据")

    # 读取课程表
    courses = []
    ws = wb[sheets_found["课程"]]
    rows = list(ws.iter_rows(values_only=True))
    if len(rows) < 2:
        errors.append("「课程」表没有数据行")
    else:
        headers = [str(h).strip() if h else "" for h in rows[0]]
        for col in required_sheets["课程"]:
            if col not in headers:
                errors.append(f"「课程」表缺少列：{col}")
        if not errors:
            cid_idx = headers.index("班级ID")
            subj_idx = headers.index("学科")
            weekly_idx = headers.index("周课时")
            tid_idx = headers.index("教师ID") if "教师ID" in headers else None
            block_idx = headers.index("连堂节数") if "连堂节数" in headers else None

            fixed_subjects = {"班会", "研究性学习", "校本课", "自习"}
            unknown_class_ids = set()
            unknown_teacher_ids = set()

            for r in rows[1:]:
                if r is None or all(c is None for c in r):
                    continue
                cid = str(r[cid_idx]).strip() if r[cid_idx] else ""
                subj = str(r[subj_idx]).strip() if r[subj_idx] else ""
                if not cid or not subj:
                    continue

                # 检查班级ID
                if cid not in classes:
                    unknown_class_ids.add(cid)

                # 检查教师ID（固定课可以留空）
                if tid_idx is not None:
                    tid = str(r[tid_idx]).strip() if r[tid_idx] else ""
                    if tid and tid not in teachers and subj not in fixed_subjects:
                        unknown_teacher_ids.add(tid)

                # 检查周课时
                try:
                    weekly = int(float(str(r[weekly_idx]).strip()))
                    if weekly <= 0:
                        errors.append(f"班级 {cid} 的「{subj}」周课时应为正整数")
                except (TypeError, ValueError):
                    errors.append(f"班级 {cid} 的「{subj}」周课时格式错误")

                # 检查连堂节数
                if block_idx is not None:
                    try:
                        block = int(float(str(r[block_idx]).strip())) if r[block_idx] else 0
                        if block not in (0, 2):
                            warnings.append(f"班级 {cid} 的「{subj}」连堂节数为 {block}，建议填0或2")
                    except (TypeError, ValueError):
                        pass

                courses.append({"class_id": cid, "subject": subj})

            stats["courses"] = len(courses)

            if unknown_class_ids:
                errors.append(f"课程表中有 {len(unknown_class_ids)} 个班级ID在班级表中不存在：{', '.join(sorted(unknown_class_ids)[:5])}")
            if unknown_teacher_ids:
                warnings.append(f"课程表中有 {len(unknown_teacher_ids)} 个教师ID在教师表中不存在：{', '.join(sorted(unknown_teacher_ids)[:5])}")

            # 检查每个班是否有课程
            for cid in classes:
                class_courses = [c for c in courses if c["class_id"] == cid]
                if not class_courses:
                    warnings.append(f"班级 {classes[cid]['name']} 没有任何课程记录")

    wb.close()

    # 统计学科
    all_subjects = set(c["subject"] for c in courses)
    stats["subjects"] = sorted(all_subjects)

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "stats": stats,
    }

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(json.dumps({"valid": False, "errors": ["缺少文件路径参数"], "warnings": [], "stats": {}}))
        sys.exit(1)
    result = validate(sys.argv[1])
    print(json.dumps(result, ensure_ascii=False))
