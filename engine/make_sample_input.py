# -*- coding: utf-8 -*-
"""生成最小可用测试 input.xlsx（协议 §10 要求算法团队交付一份供外壳联调）。

用法: python make_sample_input.py <输出路径.xlsx> [班级数]

新格式（4 个 sheet，「任课安排」可选）：
  教师 | 班级 | 课时标准 | 任课安排（可选）
本脚本**故意不生成「任课安排」**，用来覆盖「不指定教师、由引擎决定任课」这条路径。

说明：数据全部为占位符（团队约定不写真实教师/学生姓名），教师用 T01/T02… 编号。
"""
import sys

from openpyxl import Workbook

# (学科, 选考周课时, 非选考周课时, 连堂节数, 连堂日, 教师数)
# 连堂日 = 第几天连堂（1=周一…5=周五）；连堂只在第 1-2 节或第 4-5 节。
STANDARDS = [
    ("语文", 6, 6, 2, 3, 2),
    ("数学", 6, 6, 2, 2, 2),
    ("英语", 6, 6, 2, 4, 2),
    ("物理", 5, 0, 0, 0, 1),
    ("化学", 4, 0, 0, 0, 1),
    ("生物", 4, 0, 0, 0, 1),
    ("政治", 4, 0, 0, 0, 1),
    ("历史", 4, 0, 0, 0, 1),
    ("地理", 4, 0, 0, 0, 1),
    ("体育", 2, 2, 0, 0, 1),
    ("艺术", 1, 1, 0, 0, 1),
    ("班会", 1, 1, 0, 0, 0),
    ("研究性学习", 1, 1, 0, 0, 0),
    ("校本课", 1, 1, 0, 0, 0),
]

# 选科组合（字符须是 物/化/生/政/史/地 之一），依次分配给各班
ELECTIVES = ["物化生", "物化地", "政史地", "史地生"]


def build(path: str, n_classes: int = 4) -> None:
    wb = Workbook()

    # ---- 教师 ----
    ws = wb.active
    ws.title = "教师"
    ws.append(["教师ID", "教师姓名", "任教学科", "职务"])
    # 先按学科铺开教师，再回填班主任：班主任取前 n_classes 位，且必须真实存在
    teacher_ids = []          # [(教师ID, 学科)]
    tid = 1
    for subj, _, _, _, _, cnt in STANDARDS:
        for _ in range(cnt):
            t = f"T{tid:02d}"
            ws.append([t, f"教师{tid:02d}", subj, None])
            teacher_ids.append((t, subj))
            tid += 1
    if n_classes > len(teacher_ids):
        raise SystemExit(f"班级数不能超过教师数 {len(teacher_ids)}")

    # ---- 班级 ----
    ws = wb.create_sheet("班级")
    ws.append(["班级ID", "班级名称", "年级", "班主任", "选科"])
    for i in range(n_classes):
        ws.append([
            f"C{i + 1:02d}",
            f"高二({i + 1})班",
            "高二",
            teacher_ids[i][0],                    # 班主任
            ELECTIVES[i % len(ELECTIVES)],
        ])

    # ---- 课时标准 ----
    ws = wb.create_sheet("课时标准")
    ws.append(["学科", "选考周课时", "非选考周课时", "连堂节数", "连堂日"])
    for subj, elec, non_elec, block, day, _ in STANDARDS:
        ws.append([subj, elec, non_elec, block, day])

    # ---- 自检：每班周课时合计不得超过 5 天 × 8 节 = 40 ----
    subj_map = {"物": "物理", "化": "化学", "生": "生物",
                "政": "政治", "史": "历史", "地": "地理"}
    for i in range(n_classes):
        chosen = {subj_map[ch] for ch in ELECTIVES[i % len(ELECTIVES)] if ch in subj_map}
        total = sum((elec if subj in chosen else non_elec)
                    for subj, elec, non_elec, _, _, _ in STANDARDS)
        if total > 40:
            raise SystemExit(f"班级 C{i + 1:02d} 周课时合计 {total} 超过 40，请调低课时标准")

    wb.save(path)
    print(f"已生成 {path}（{n_classes} 个班，{len(teacher_ids)} 位教师，未含「任课安排」）")


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "sample_input.xlsx"
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 4
    build(out, n)
