# -*- coding: utf-8 -*-
"""特殊要求文本解析探针：确认「自然语言写的要求」不再被静默丢弃。

背景（2026-10-03 用户实测）：输入两行
    T030教C05英语
    T48不想周五上课
只有第一行生效，第二行**连告警都没有** —— 用户以为生效了，实际没有。
根因在 load.py 的 2c 判定：只认「含 教/=」与「以 1. ① - 开头」两种，
自然语言写的要求两条都不命中，直接 continue 丢掉。

本脚本直接调解析函数，逐条断言归类结果。
用法：python text_parse_probe.py
"""
import io
import os
import sys
from types import SimpleNamespace as NS

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# 用相对位置定位仓库根（脚本在 测试存档/<日期-主题>/复现脚本/ 下）
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, os.path.join(REPO, "engine"))

from scheduler.data.load import _parse_teacher_constraints  # noqa: E402

# 造一份最小教师/班级表（只要 name / subject / id 三个属性，鸭子类型即可）
teachers = {"T%03d" % i: NS(name="老师%03d" % i, subject="语文") for i in range(1, 50)}
teachers["T048"] = NS(name="英语老师048", subject="英语")
classes = [NS(id="C%02d" % i, name="高二(%d)班" % i) for i in range(1, 26)]

# (该行文本, 期望归类, 说明)
#   parsed      = 成功解析成教师指定
#   unparsed    = 报「像是教师指定但无法解析」
#   unsupported = 报「这一行没有生效」
#   silent      = 当作普通说明文字，不打扰（正确的静默）
CASES = [
    ("T030教C05英语", "parsed", "教师指定（受支持的写法）"),
    ("T001=C01语文", "parsed", "教师指定（等号写法）"),
    ("T048不想周五上课", "unsupported", "★ 用户实测的那一行（修复前被静默丢弃）"),
    ("T999教C05英语", "unparsed", "教师不存在 → 提示"),
    ("T001教C99语文", "unparsed", "班级不存在 → 提示"),
    ("张老师周二没空", "unsupported", "姓名 + 星期"),
    ("C01班周五第7-8节不排课", "unsupported", "班级 + 星期 + 节次"),
    ("T030最多带2个班", "unsupported", "数量限制"),
    ("数学老师请假", "unsupported", "要求动词"),
    ("张老师不想周二上连堂", "unsupported", "星期 + 连堂"),
    ("年级：高二", "silent", "普通说明文字"),
    ("2026年秋季作息时间", "silent", "以数字开头的说明"),
    ("3月1日开始执行", "silent", "以数字开头的说明"),
    ("本表由教务处维护", "silent", "普通说明文字"),
    ("备注：体育课在操场", "silent", "普通说明文字"),
    ("12班语文每周加一节", "silent", "已知边界：不含明显特征词，仍不报（见说明.md）"),
]

print("=" * 78)
print("特殊要求解析探针 —— 仓库：%s" % REPO)
print("=" * 78)

bad = 0
for line, want, desc in CASES:
    res, unparsed, unsupported = _parse_teacher_constraints(line, teachers, classes)
    if res:
        got = "parsed"
    elif unparsed:
        got = "unparsed"
    elif unsupported:
        got = "unsupported"
    else:
        got = "silent"
    ok = got == want
    if not ok:
        bad += 1
    print("  %s %-26s → %-12s (期望 %-12s) %s"
          % ("[OK]" if ok else "[X] ", line, got, want, desc))

print()
print("=" * 78)
print("  共 %d 条：%s" % (len(CASES), "全部符合预期" if bad == 0 else "%d 条不符合预期" % bad))
print("=" * 78)
sys.exit(1 if bad else 0)
