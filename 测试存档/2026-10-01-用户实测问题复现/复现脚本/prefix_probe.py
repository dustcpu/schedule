# -*- coding: utf-8 -*-
"""行首编号对「特殊要求」解析的影响 —— 回归用例。

历史：
  2026-10-01 问题 2a：解析器不剥行首列表标记，`1.T30教C05数学` 里第一个「教」之前
  成了 `1.T30`，匹配不到教师 → 整行丢弃（连受支持的写法都失效）。
  同日 `5b03d06` 修复。

  ⚠️ 同一次修复还改了返回值签名：`_parse_teacher_constraints` 自 2026-10-01 起
     返回 **3 元组** `(result, unparsed, unsupported)`。

运行：python prefix_probe.py
预期：A 组编号写法全部「生效」；B 组普通文字「静默」不打扰；C 组明确告警。
"""
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


def probe(line):
    res, unparsed, unsupported = _parse_teacher_constraints(line, TEACHERS, CLASSES)
    if res:
        return "✔ 生效", str(res)
    if unparsed:
        return "△ 语法告警", unparsed[0][:36]
    if unsupported:
        return "△ 不支持告警", unsupported[0][:36]
    return "· 静默（当普通说明）", ""


GROUPS = [
    ("A. 各种行首编号（都应当生效）", [
        "T30教C05数学",
        "1.T30教C05数学",
        "1、T30教C05数学",
        "1）T30教C05数学",
        "(1)T30教C05数学",
        "①T30教C05数学",
        "- T30教C05数学",
        "1.T30=C05数学",
        "1.张三教五班数学",
    ]),
    ("B. 以数字开头的普通文字（应当静默，不打扰用户）", [
        "2026年秋季作息时间",
        "3月1日开始执行",
        "12班语文每周加一节",
        "2024级选科说明",
        "年级：高二",
    ]),
    ("C. 会被明确告警的两类", [
        "2.教师T30所教授的班级其中一个必须是C05",
        "1.非物理选科班每周安排一节物理，非政治选科班每周安排一节政治",
    ]),
]

for title, cases in GROUPS:
    print("=" * 84)
    print(title)
    for line in cases:
        verdict, detail = probe(line)
        print(f"  {line:34s} {verdict:16s} {detail}")
