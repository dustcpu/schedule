# -*- coding: utf-8 -*-
"""词典自测：合成数据（占位命名，无真实姓名）逐条断言解析结果。

运行：python 约束转换器/测试语料/自测.py
全绿输出「自测通过 N 条」；任何一条不符则以退出码 1 结束。
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from 词典 import ParseContext, parse_line, describe_constraint  # noqa: E402


class FakeContext(ParseContext):
    """合成教师/班级表（占位命名），与 engine/test_input_25_new 的口径类似。"""

    def __init__(self):
        self.teachers = {
            "T001": {"name": "语文老师001", "subject": "语文"},
            "T002": {"name": "语文老师002", "subject": "语文"},
            "T013": {"name": "数学老师013", "subject": "数学"},
            "T030": {"name": "数学老师030", "subject": "数学"},
            "T045": {"name": "英语老师045", "subject": "英语"},
            "T077": {"name": "张卫东", "subject": "物理"},
            "T088": {"name": "李梅", "subject": "化学"},
        }
        self.classes = {
            "C01": "高二(1)班", "C05": "高二(5)班", "C12": "高二(12)班",
        }
        self._day_of = {"语文": 3, "数学": 2, "英语": 4}

    def resolve_teacher(self, token):
        token = (token or "").strip()
        if not token:
            return None, None
        if token in self.teachers:
            return token, None
        # 容忍 T30 ↔ T030（与引擎 _resolve_id 同口径）
        m = re.match(r"^([A-Za-z]*)(\d+)$", token)
        if m:
            key = (m.group(1).upper(), m.group(2).lstrip("0"))
            for tid in self.teachers:
                m2 = re.match(r"^([A-Za-z]*)(\d+)$", tid)
                if m2 and (m2.group(1).upper(), m2.group(2).lstrip("0")) == key:
                    return tid, None
        name = token[:-2] if token.endswith("老师") else token
        for tid, info in self.teachers.items():
            if info["name"] == name:
                return tid, None
        return None, f"「{token}」不在「教师」表里"

    def resolve_class(self, token):
        token = (token or "").strip()
        if token in self.classes:
            return token, None
        hits = [cid for cid, nm in self.classes.items() if token in nm or nm == token]
        if len(hits) == 1:
            return hits[0], None
        if len(hits) > 1:
            return None, f"「{token}」对应多个班级"
        return None, f"「{token}」不在「班级」表里"

    def match_subject(self, subj_raw, tid):
        subj_raw = (subj_raw or "").strip()
        t = self.teachers.get(tid)
        if t and t["subject"] and t["subject"] in subj_raw:
            return t["subject"]
        subs = sorted({v["subject"] for v in self.teachers.values()},
                      key=len, reverse=True)
        for s in subs:
            if s in subj_raw:
                return s
        return None

    def teacher_display(self, tid):
        info = self.teachers.get(tid)
        return f"{info['name']}（{tid}）" if info else tid

    def teacher_info(self, tid):
        return self.teachers.get(tid)

    def double_day_of(self, subject):
        return self._day_of.get(subject)


CTX = FakeContext()


def c_type(r):
    return r["constraints"][0]["type"] if r["status"] == "ok" else None


def c_body(r):
    return r["constraints"][0] if r["status"] == "ok" else None


# (行, 期望 status, 期望 type, 期望约束字段, 必须出现的预警关键词)
CASES = [
    # ---- 指定任课 ----
    ("T30教C05数学", "ok", "assign_teacher",
     {"teacher": "T030", "class": "C05", "subject": "数学"}, []),
    ("张卫东教高二(1)班物理", "ok", "assign_teacher",
     {"teacher": "T077", "class": "C01", "subject": "物理"}, []),
    ("1.T002教C12语文", "ok", "assign_teacher",
     {"teacher": "T002", "class": "C12", "subject": "语文"}, []),
    ("李梅=C05化学", "ok", "assign_teacher",
     {"teacher": "T088", "class": "C05", "subject": "化学"}, []),
    # ---- 教师某天/某节不可用 ----
    ("张卫东周二没空", "ok", "teacher_unavailable",
     {"teacher": "T077", "days": [2]}, []),
    ("李梅周四下午不排课", "ok", "teacher_unavailable",
     {"teacher": "T088", "days": [4], "periods": [6, 7, 8]}, []),
    ("T045周五第1-2节请假", "ok", "teacher_unavailable",
     {"teacher": "T045", "days": [5], "periods": [1, 2]}, []),
    ("2、数学老师030星期三有事", "ok", "teacher_unavailable",
     {"teacher": "T030", "days": [3]}, []),
    ("语文老师001礼拜五不安排课", "ok", "teacher_unavailable",
     {"teacher": "T001", "days": [5]}, []),
    ("李梅不想上周二的课", "ok", "teacher_unavailable",
     {"teacher": "T088", "days": [2]}, []),
    ("语文老师001周一第1节和第2节不排课", "ok", "teacher_unavailable",
     {"teacher": "T001", "days": [1], "periods": [1, 2]}, []),
    # ---- 教师连堂禁日（核心示例）----
    ("张卫东不想周二上连堂", "ok", "teacher_no_double_day",
     {"teacher": "T077", "days": [2]}, []),
    ("数学老师013不想周二上连堂", "ok", "teacher_no_double_day",
     {"teacher": "T013", "days": [2]}, ["数学", "连堂固定在周二"]),
    ("语文老师001不想周三连排", "ok", "teacher_no_double_day",
     {"teacher": "T001", "days": [3]}, ["语文", "周三"]),
    ("3. T045周四不要连堂", "ok", "teacher_no_double_day",
     {"teacher": "T045", "days": [4]}, []),
    ("英语老师045连堂避开周五", "ok", "teacher_no_double_day",
     {"teacher": "T045", "days": [5]}, []),  # 英语连堂在周四，禁周五无冲突
    ("T045不想周四上连堂", "ok", "teacher_no_double_day",
     {"teacher": "T045", "days": [4]}, ["英语", "周四"]),
    # ---- 班级某天/某节不可用 ----
    ("C01班周五第7-8节不排课", "ok", "class_unavailable",
     {"class": "C01", "day": 5, "periods": [7, 8]}, []),
    ("高二(5)班周一上午安排活动", "ok", "class_unavailable",
     {"class": "C05", "day": 1, "periods": [1, 2, 3, 4, 5]}, []),
    ("4、C12周二考试", "ok", "class_unavailable",
     {"class": "C12", "day": 2}, []),
    ("C01班周一和周三下午不排课", "ok", None, {"_count": 2}, []),
    # ---- 教师带班数上限 ----
    ("张卫东最多带2个班", "ok", "teacher_max_classes",
     {"teacher": "T077", "max": 2}, []),
    ("数学老师030不能超过3个班", "ok", "teacher_max_classes",
     {"teacher": "T030", "max": 3}, []),
    # ---- 认不出（弃权，不硬猜）----
    ("教师T30所教授的班级其中一个必须是C05", "unknown", None, {}, []),
    ("张三教五班数学", "unknown", None, {}, ["张三"]),
    ("非物理选科班每周安排一节物理", "unknown", None, {}, []),
    ("李梅上周二的课", "unknown", None, {}, []),
]


def main():
    fails = []
    for raw, want_status, want_type, want_fields, note_kws in CASES:
        r = parse_line(raw, CTX)
        tag = f"「{raw}」"
        if r["status"] != want_status:
            fails.append(f"{tag} status={r['status']} 期望 {want_status}"
                         f"（reason={r['reason']}）")
            continue
        if want_status != "ok":
            for kw in note_kws:
                if kw not in r["reason"]:
                    fails.append(f"{tag} 原因里没有「{kw}」：{r['reason']}")
            continue
        if want_fields.get("_count") is not None:
            if len(r["constraints"]) != want_fields["_count"]:
                fails.append(f"{tag} 拆出 {len(r['constraints'])} 条，"
                             f"期望 {want_fields['_count']}")
            continue
        body = c_body(r)
        if body["type"] != want_type:
            fails.append(f"{tag} type={body['type']} 期望 {want_type}")
            continue
        for k, v in want_fields.items():
            if body.get(k) != v:
                fails.append(f"{tag} {k}={body.get(k)!r} 期望 {v!r}")
        for kw in note_kws:
            if not any(kw in n for n in r["notes"]):
                fails.append(f"{tag} 缺少预警「{kw}」，实际 notes={r['notes']}")
        # 回显必须非空且带"理解"口径
        if not r["echo"]:
            fails.append(f"{tag} 没有回显")

    # 回显抽查
    echo = describe_constraint({"type": "teacher_no_double_day",
                                "teacher": "T013", "days": [2]}, CTX)
    if "数学老师013" not in echo or "周二" not in echo:
        fails.append(f"回显异常：{echo}")

    # 空行 / 编号行
    r = parse_line("   ", CTX)
    if r["status"] != "empty":
        fails.append("空行应返回 empty")
    r = parse_line("5. （说明）本学期共20周", CTX)
    if r["status"] == "ok":
        fails.append(f"普通说明被误判为约束：{r}")

    if fails:
        print(f"自测未通过（{len(fails)} 处）：")
        for f in fails:
            print("  ✗", f)
        sys.exit(1)
    print(f"自测通过：{len(CASES) + 3} 条用例全绿")


if __name__ == "__main__":
    main()
