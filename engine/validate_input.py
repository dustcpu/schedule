# -*- coding: utf-8 -*-
"""输入文件校验：新格式（教师 / 班级 / 课时标准 / 任课安排）。

旧的 3-sheet 格式（班级 / 教师 / 课程）已不再支持，检测到会直接给出提示。
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
    if not path.lower().endswith((".xlsx", ".xlsm")):
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

    def find_sheet(names):
        for n in names:
            for sn in sheet_names:
                if sn.strip() == n:
                    return sn
        return None

    def read_rows(ws):
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            return [], []
        headers = [str(h).strip() if h else "" for h in rows[0]]
        data = []
        for r in rows[1:]:
            if r is None or all(c is None or str(c).strip() == "" for c in r):
                continue
            d = {}
            for i, h in enumerate(headers):
                if h:
                    d[h] = r[i] if i < len(r) else None
            data.append(d)
        return headers, data

    period_sheet = find_sheet(["课时标准", "课时"])
    stats["format"] = "新格式（课时标准）"

    # 旧格式直接拒绝：先给一句明确的话，不要再叠一堆列缺失的错误。
    # 识别旧格式只能用「课程」——「课时」是「课时标准」的别名，用它判定会误判。
    if period_sheet is None:
        wb.close()
        if find_sheet(["课程", "课程表"]) is not None:
            return {"valid": False, "warnings": [], "stats": stats,
                    "errors": ["检测到旧格式输入（班级/教师/课程），本版本已不再支持。"
                               "请改用新格式：教师 / 班级 / 课时标准 / 任课安排（任课安排可选），"
                               "可从软件「下载模板」获取新格式模板后重新填写。"]}
        return {"valid": False, "warnings": [], "stats": stats,
                "errors": ["缺少「课时标准」工作表。新格式需要："
                           "教师 / 班级 / 课时标准（任课安排可选）。"]}

    teacher_sheet = find_sheet(["教师", "教师表"])
    teachers = {}
    if teacher_sheet is None:
        errors.append("缺少「教师」工作表")
    else:
        headers, rows = read_rows(wb[teacher_sheet])
        for col in ["教师ID", "任教学科", "教师姓名"]:
            if col not in headers:
                errors.append(f"「教师」表缺少列：{col}")
        if not errors:
            for r in rows:
                tid = str(r.get("教师ID") or "").strip()
                if not tid:
                    continue
                teachers[tid] = {
                    "name": str(r.get("教师姓名") or "").strip() or tid,
                    "subject": str(r.get("任教学科") or "").strip(),
                }
            stats["teachers"] = len(teachers)
            # 给前端「书写规范」当真实示例：用户照文档写 T001，而他表里是 T30，
            # 结果整条特殊要求被忽略（2026-10-01 实测）。示例必须来自他自己的表。
            stats["teacher_ids"] = list(teachers)[:3]
            if len(teachers) == 0:
                errors.append("「教师」表没有有效数据")

    class_sheet = find_sheet(["班级", "班级表"])
    classes = {}
    if class_sheet is None:
        errors.append("缺少「班级」工作表")
    else:
        headers, rows = read_rows(wb[class_sheet])
        for col in ["班级ID", "班级名称", "年级"]:
            if col not in headers:
                errors.append(f"「班级」表缺少列：{col}")
        if not errors:
            for r in rows:
                cid = str(r.get("班级ID") or "").strip()
                if not cid:
                    continue
                classes[cid] = {
                    "name": str(r.get("班级名称") or "").strip() or cid,
                    "elective": str(r.get("选科") or "").strip(),
                }
            stats["classes"] = len(classes)
            stats["class_ids"] = list(classes)[:3]
            if len(classes) == 0:
                errors.append("「班级」表没有有效数据")

    if errors:
        wb.close()
        return {"valid": False, "errors": errors, "warnings": warnings, "stats": stats}

    headers, rows = read_rows(wb[period_sheet])
    for col in ["学科", "选考周课时", "非选考周课时"]:
        if col not in headers:
            errors.append(f"「课时标准」表缺少列：{col}")
    if not errors:
        standards = {}
        for r in rows:
            subj = str(r.get("学科") or "").strip()
            if not subj:
                continue
            try:
                elec = int(float(str(r.get("选考周课时") or 0)))
                non_elec = int(float(str(r.get("非选考周课时") or 0)))
            except (TypeError, ValueError):
                errors.append(f"「课时标准」表中「{subj}」的课时格式错误")
                continue
            standards[subj] = {"elective": elec, "non_elective": non_elec}
        stats["subjects"] = len(standards)
        if len(standards) == 0:
            errors.append("「课时标准」表没有有效数据")

        subj_map = {"物": "物理", "化": "化学", "生": "生物", "政": "政治", "史": "历史", "地": "地理"}
        for cid, ci in classes.items():
            elective_set = set()
            if ci["elective"]:
                for ch in ci["elective"]:
                    if ch in subj_map:
                        elective_set.add(subj_map[ch])
            total = 0
            for subj, s in standards.items():
                weekly = s["elective"] if subj in elective_set else s["non_elective"]
                total += weekly
            if total > 40:
                errors.append(f"班级 {ci['name']} 周课时合计 {total} 超过可用格数 40")
            elif total == 40:
                warnings.append(f"班级 {ci['name']} 周课时刚好 40，没有自习时间")

    assign_sheet = find_sheet(["任课安排", "任课"])
    if assign_sheet:
        headers, rows = read_rows(wb[assign_sheet])
        unknown_t = set()
        unknown_c = set()
        for r in rows:
            tid = str(r.get("教师ID") or "").strip()
            cid = str(r.get("班级ID") or "").strip()
            if tid and tid not in teachers:
                unknown_t.add(tid)
            if cid and cid not in classes:
                unknown_c.add(cid)
        if unknown_t:
            warnings.append(f"任课安排中有 {len(unknown_t)} 个教师ID在教师表中不存在")
        if unknown_c:
            warnings.append(f"任课安排中有 {len(unknown_c)} 个班级ID在班级表中不存在")
        stats["assignments"] = len(rows)
    else:
        warnings.append("没有「任课安排」表，将按学科自动分配教师")

    wb.close()
    return {"valid": len(errors) == 0, "errors": errors, "warnings": warnings, "stats": stats}


def _emit(payload) -> None:
    """把结果按 UTF-8 写到 stdout。

    协议约定外壳按 UTF-8 读取；但 Windows 下 Python 往管道写时会退回系统
    ANSI 代码页（简体中文 = cp936），中文到外壳那边就成了乱码。
    这里直接写 UTF-8 字节，不依赖 locale / PYTHONIOENCODING 是否设对。
    """
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    buf = getattr(sys.stdout, "buffer", None)
    if buf is not None:
        buf.write(data)
        buf.flush()
    else:
        sys.stdout.write(data.decode("utf-8", "replace"))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        _emit({"valid": False, "errors": ["缺少文件路径"], "warnings": [], "stats": {}})
        sys.exit(1)
    _emit(validate(sys.argv[1]))
