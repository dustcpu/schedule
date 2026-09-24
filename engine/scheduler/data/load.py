# -*- coding: utf-8 -*-
"""数据加载层：读 input.xlsx、hard_limits.json、requirements.txt。

input.xlsx 模板（由算法团队定义，协议 §10 要求交付模板及字段说明）：

  Sheet「班级」：班级ID | 班级名称 | 年级 | 科类
  Sheet「教师」：教师ID | 教师姓名 | 任教学科
  Sheet「课程」：班级ID | 学科 | 教师ID | 周课时 | 连堂节数 | 单双周

说明：
- 「课程」表以 (班级ID, 学科) 为一行，表示该班该学科的周课时与任课教师。
- 连堂节数：0/2，默认 0；非 0 时该学科按连堂建模（受 hard_limits.consecutive 约束）。
- 单双周：每周 / 单周 / 双周，默认「每周」。v1 内核按每周排课，
  若出现「单周/双周」会在 warnings 中如实提示（R5 待团队拍板后启用周维度）。
- 自习：可在「课程」表里写学科名「自习」（教师ID 留空）指定课时；
  未指定时由内核用剩余格子自动补齐（config.solver.self_study_fill）。
"""
import json
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any

from openpyxl import load_workbook

from ..config import (
    Config, SELF_STUDY, _norm_header,
    load_from_hard_limits,
)


class DataError(Exception):
    """输入数据错误，交由上层转成 code=2。"""


@dataclass
class ClassInfo:
    id: str
    name: str = ""
    grade: str = ""
    track: str = ""   # 科类：文科 / 理科


@dataclass
class CourseReq:
    """一个班一门学科的课时需求。"""
    class_id: str
    subject: str
    teacher_id: Optional[str]   # None 表示无固定教师（班会/自习等）
    weekly: int                 # 周课时（节）
    block_len: int = 0          # 连堂节数（0=不连堂，2=两节连堂）
    week_mode: str = "每周"      # 每周 / 单周 / 双周


@dataclass
class Problem:
    classes: List[ClassInfo] = field(default_factory=list)
    teachers: Dict[str, str] = field(default_factory=dict)   # id -> 姓名
    courses: List[CourseReq] = field(default_factory=list)
    requirements: str = ""
    config: Config = field(default_factory=Config)
    warnings: List[str] = field(default_factory=list)

    def teacher_name(self, tid: Optional[str]) -> str:
        if not tid:
            return ""
        return self.teachers.get(tid, tid)


# ---------------------------------------------------------------- 通用读取
def _find_sheet(wb, names: List[str]):
    """按候选名找 sheet（容错：忽略空格）。"""
    norm = {_norm_header(s): s for s in wb.sheetnames}
    for n in names:
        if _norm_header(n) in norm:
            return wb[norm[_norm_header(n)]]
    return None


def _read_rows(ws) -> List[Dict[str, Any]]:
    """以第一行作表头，读成 dict 列表；跳过全空行。"""
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []
    headers = [_norm_header(h) for h in rows[0]]
    out = []
    for r in rows[1:]:
        if r is None or all(c is None or str(c).strip() == "" for c in r):
            continue
        d = {}
        for i, h in enumerate(headers):
            if not h:
                continue
            d[h] = r[i] if i < len(r) else None
        out.append(d)
    return out


def _cell_str(v) -> str:
    return "" if v is None else str(v).strip()


def _cell_int(v, default: int = 0) -> int:
    if v is None or str(v).strip() == "":
        return default
    try:
        return int(float(str(v).strip()))
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------- 各表解析
def _load_classes(ws) -> List[ClassInfo]:
    if ws is None:
        raise DataError("input.xlsx 缺少「班级」sheet")
    rows = _read_rows(ws)
    if not rows:
        raise DataError("「班级」sheet 为空，至少需要 1 个班级")
    out: List[ClassInfo] = []
    seen = set()
    for r in rows:
        cid = _cell_str(r.get("班级ID") or r.get("班级编号"))
        if not cid:
            continue
        if cid in seen:
            raise DataError(f"「班级」sheet 中班级ID 重复：{cid}")
        seen.add(cid)
        out.append(ClassInfo(
            id=cid,
            name=_cell_str(r.get("班级名称") or r.get("班级")),
            grade=_cell_str(r.get("年级")),
            track=_cell_str(r.get("科类")),
        ))
    if not out:
        raise DataError("「班级」sheet 未解析到有效行（缺少「班级ID」列？）")
    return out


def _load_teachers(ws) -> Dict[str, str]:
    if ws is None:
        # 教师表允许缺省（全部课程不指定教师时不冲突），但通常应有
        return {}
    out: Dict[str, str] = {}
    for r in _read_rows(ws):
        tid = _cell_str(r.get("教师ID") or r.get("教师编号"))
        if not tid:
            continue
        out[tid] = _cell_str(r.get("教师姓名") or r.get("姓名")) or tid
    return out


def _load_courses(ws, class_ids: List[str]) -> List[CourseReq]:
    if ws is None:
        raise DataError("input.xlsx 缺少「课程」sheet")
    rows = _read_rows(ws)
    if not rows:
        raise DataError("「课程」sheet 为空，无法排课")
    known = set(class_ids)
    out: List[CourseReq] = []
    seen = set()
    for r in rows:
        cid = _cell_str(r.get("班级ID") or r.get("班级编号"))
        subj = _cell_str(r.get("学科") or r.get("科目"))
        if not cid or not subj:
            continue
        if cid not in known:
            raise DataError(f"「课程」sheet 出现未知班级ID：{cid}（请与「班级」sheet 对齐）")
        key = (cid, subj)
        if key in seen:
            raise DataError(f"「课程」sheet 中 (班级 {cid}, 学科 {subj}) 重复")
        seen.add(key)
        tid = _cell_str(r.get("教师ID") or r.get("教师编号"))
        weekly = _cell_int(r.get("周课时"), 0)
        if weekly <= 0:
            raise DataError(f"班级 {cid} 的「{subj}」周课时应为正整数，当前为 {weekly}")
        block_len = _cell_int(r.get("连堂节数"), 0)
        wm = _cell_str(r.get("单双周")) or "每周"
        out.append(CourseReq(
            class_id=cid,
            subject=subj,
            teacher_id=tid or None,
            weekly=weekly,
            block_len=block_len if block_len > 0 else 0,
            week_mode=wm,
        ))
    if not out:
        raise DataError("「课程」sheet 未解析到有效行（缺少「班级ID」/「学科」列？）")
    return out


# ---------------------------------------------------------------- 入口
def load_problem(in_dir: str) -> Problem:
    """读取输入目录，构造 Problem。出错抛 DataError（上层转 code=2）。"""
    # 1) 定位 input.xlsx（外壳固定命名，但容错 input.*，见 main.rs 行 303-309）
    xlsx_path = None
    for fn in sorted(os.listdir(in_dir)):
        if fn.lower().startswith("input.") and fn.lower().endswith((".xlsx", ".xlsm")):
            xlsx_path = os.path.join(in_dir, fn)
            break
    if xlsx_path is None:
        raise DataError("输入目录里没有 input.xlsx")

    wb = load_workbook(xlsx_path, data_only=True, read_only=True)

    classes = _load_classes(_find_sheet(wb, ["班级", "班级表", "classes"]))
    teachers = _load_teachers(_find_sheet(wb, ["教师", "教师表", "teachers"]))
    courses = _load_courses(_find_sheet(wb, ["课程", "课程表", "课时", "courses"]),
                            [c.id for c in classes])
    wb.close()

    # 2) hard_limits.json（外壳会写；协议未收录，故可选）
    cfg = Config()
    hl_path = os.path.join(in_dir, "hard_limits.json")
    if os.path.exists(hl_path):
        try:
            with open(hl_path, "r", encoding="utf-8-sig") as f:
                hl = json.load(f)
            cfg = load_from_hard_limits(hl)
        except Exception as e:
            # 配置坏了不应让整个排课失败，退回默认并警告
            cfg = Config()
            warnings_list = [f"hard_limits.json 解析失败，已改用默认作息/固定课：{e}"]
        else:
            warnings_list = []
    else:
        warnings_list = []

    # 3) requirements.txt
    requirements = ""
    req_path = os.path.join(in_dir, "requirements.txt")
    if os.path.exists(req_path):
        try:
            with open(req_path, "r", encoding="utf-8") as f:
                requirements = f.read().strip()
        except Exception:
            requirements = ""

    p = Problem(
        classes=classes,
        teachers=teachers,
        courses=courses,
        requirements=requirements,
        config=cfg,
        warnings=warnings_list,
    )
    _post_process(p)
    return p


def _post_process(p: Problem) -> None:
    """补齐自习课时、提示单双周等。"""
    cfg = p.config
    grid = cfg.grid_size()

    # 单双周提示（v1 内核按每周排，R5 待拍板）
    alt = [c for c in p.courses if c.week_mode not in ("每周", "", None)]
    if alt:
        p.warnings.append(
            f"检测到 {len(alt)} 条「单周/双周」课程，v1 内核暂按每周排课"
            f"（单双周字段待团队拍板后启用，见风险 R5）"
        )

    # 自习补齐
    if cfg.solver.self_study_fill:
        for ci in p.classes:
            has_ss = any(c.subject == SELF_STUDY and c.class_id == ci.id for c in p.courses)
            if has_ss:
                continue
            used = sum(c.weekly for c in p.courses if c.class_id == ci.id)
            rest = grid - used
            if rest > 0:
                p.courses.append(CourseReq(
                    class_id=ci.id, subject=SELF_STUDY, teacher_id=None,
                    weekly=rest, block_len=0, week_mode="每周",
                ))
