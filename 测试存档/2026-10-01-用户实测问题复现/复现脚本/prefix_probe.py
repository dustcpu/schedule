# -*- coding: utf-8 -*-
"""进一步验证：行首的编号（1. / 1、/ ① / —— 等）会不会破坏教师指定解析。"""
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))   # 仓库根
sys.path.insert(0, os.path.join(REPO, "engine"))

from scheduler.data.load import _parse_teacher_constraints, TeacherInfo, ClassInfo

TEACHERS = {
    "T30": TeacherInfo(id="T30", name="张三", subject="数学"),
    "T01": TeacherInfo(id="T01", name="李四", subject="语文"),
}
CLASSES = [ClassInfo(id="C01", name="一班"), ClassInfo(id="C05", name="五班")]

CASES = [
    "T30教C05数学",
    "1.T30教C05数学",
    "1、T30教C05数学",
    "1）T30教C05数学",
    "(1)T30教C05数学",
    "①T30教C05数学",
    "- T30教C05数学",
    "T30=C05数学",
    "1.T30=C05数学",
    "T30教C05",
    "张三教五班数学",
    "1.张三教五班数学",
]

print(f"{'输入行':34s} {'解析结果':22s} 判定")
print("-" * 88)
for line in CASES:
    res, unparsed = _parse_teacher_constraints(line, TEACHERS, CLASSES)
    if res:
        verdict = "✔ 解析成功"
    elif unparsed:
        verdict = "△ 只进了「无法解析」告警 → 实际被忽略"
    else:
        verdict = "★ 完全静默丢弃"
    print(f"{line:34s} {str(res):22s} {verdict}")
