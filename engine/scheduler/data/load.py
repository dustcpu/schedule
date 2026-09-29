# -*- coding: utf-8 -*-
"""数据加载层：读 input.xlsx、hard_limits.json、requirements.txt。

input.xlsx 格式（4 个 sheet，任课安排可选）：
  Sheet「教师」：教师ID | 教师姓名 | 任教学科 | 职务
  Sheet「班级」：班级ID | 班级名称 | 年级 | 班主任 | 选科（如物化生）
  Sheet「课时标准」：学科 | 选考周课时 | 非选考周课时 | 连堂节数 | 连堂日
  Sheet「任课安排」：教师ID | 班级ID | 学科（可选，默认用教师任教学科）

旧的 3-sheet 格式（班级 / 教师 / 课程）已不再支持，检测到会给出明确提示。
"""
import json
import os
import re

# 这些科目在课表上不显示教师姓名，也不需要在任课安排中指定
NO_TEACHER_SUBJECTS = {"体育", "体育活动", "艺术", "音乐", "美术", "信息技术", "通用技术", "心理", "班会", "研究性学习", "校本课", "自习"}
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Tuple

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
    elective: str = ""   # 选科组合（如 物化生），决定哪些学科按「选考周课时」计


@dataclass
class TeacherInfo:
    id: str
    name: str = ""
    subject: str = ""
    duty: str = ""


@dataclass
class PeriodStandard:
    subject: str
    elective_periods: int = 0
    non_elective_periods: int = 0
    block_len: int = 0
    consec_day: int = 0


@dataclass
class CourseReq:
    class_id: str
    subject: str
    teacher_id: Optional[str]
    weekly: int
    block_len: int = 0
    # 候选任课教师（有序，[0] 为贪心首选，用于求解热启动 hint）。
    # 空列表 = 该学科不需要教师（NO_TEACHER_SUBJECTS）。
    teacher_candidates: List[str] = field(default_factory=list)
    # True = 「任课安排」sheet 或「特殊要求」显式指派，候选恒为 1 人
    locked: bool = False

    def __post_init__(self):
        # 兼容只给 teacher_id 的构造方式（只给一位教师语义上等于显式指派）
        if not self.teacher_candidates and self.teacher_id:
            self.teacher_candidates = [self.teacher_id]
            self.locked = True
        if self.teacher_id is None and len(self.teacher_candidates) == 1:
            self.teacher_id = self.teacher_candidates[0]

    @property
    def has_teacher_decision(self) -> bool:
        """是否存在真正的教师决策空间（候选 > 1）。"""
        return len(self.teacher_candidates) > 1


@dataclass
class Problem:
    classes: List[ClassInfo] = field(default_factory=list)
    teachers: Dict[str, TeacherInfo] = field(default_factory=dict)
    courses: List[CourseReq] = field(default_factory=list)
    requirements: str = ""
    config: Config = field(default_factory=Config)
    warnings: List[str] = field(default_factory=list)
    # (班级ID, 学科) -> 教师ID：贪心得到的预期分配。
    # 仅用于求解热启动 hint、过载校验与回退路径，不是最终排课结果。
    hint_assign: Dict[Tuple[str, str], str] = field(default_factory=dict)

    def teacher_name(self, tid: Optional[str]) -> str:
        if not tid:
            return ""
        t = self.teachers.get(tid)
        return t.name if t else tid


def _find_sheet(wb, names):
    norm = {_norm_header(s): s for s in wb.sheetnames}
    for n in names:
        if _norm_header(n) in norm:
            return wb[norm[_norm_header(n)]]
    return None


def _read_rows(ws):
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


def _cell_str(v):
    return "" if v is None else str(v).strip()


def _cell_int(v, default=0):
    if v is None or str(v).strip() == "":
        return default
    try:
        return int(float(str(v).strip()))
    except (TypeError, ValueError):
        return default


def _load_classes(ws):
    if ws is None:
        raise DataError("input.xlsx 缺少「班级」sheet")
    rows = _read_rows(ws)
    if not rows:
        raise DataError("「班级」sheet 为空，至少需要 1 个班级")
    out = []
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
            elective=_cell_str(r.get("选科")),
        ))
    if not out:
        raise DataError("「班级」sheet 未解析到有效行（缺少「班级ID」列？）")
    return out


def _load_teachers(ws):
    if ws is None:
        return {}
    out = {}
    for r in _read_rows(ws):
        tid = _cell_str(r.get("教师ID") or r.get("教师编号"))
        if not tid:
            continue
        out[tid] = TeacherInfo(
            id=tid,
            name=_cell_str(r.get("教师姓名") or r.get("姓名")) or tid,
            subject=_cell_str(r.get("任教学科") or r.get("学科")),
            duty=_cell_str(r.get("职务")),
        )
    return out


def _load_period_standards(ws):
    if ws is None:
        return []
    out = []
    for r in _read_rows(ws):
        subj = _cell_str(r.get("学科") or r.get("科目"))
        if not subj:
            continue
        out.append(PeriodStandard(
            subject=subj,
            elective_periods=_cell_int(r.get("选考周课时") or r.get("选考课时"), 0),
            non_elective_periods=_cell_int(r.get("非选考周课时") or r.get("非选考课时"), 0),
            block_len=_cell_int(r.get("连堂节数"), 0),
            consec_day=_cell_int(r.get("连堂日"), 0),
        ))
    return out


def _load_teaching_assignments(ws):
    if ws is None:
        return []
    out = []
    for r in _read_rows(ws):
        tid = _cell_str(r.get("教师ID") or r.get("教师编号"))
        cid = _cell_str(r.get("班级ID") or r.get("班级编号"))
        if not tid or not cid:
            continue
        out.append({
            "teacher_id": tid,
            "class_id": cid,
            "subject": _cell_str(r.get("学科") or r.get("科目")),
        })
    return out


def _parse_teacher_constraints(text, teachers, classes):
    """从额外约束文本中解析教师指定。

    返回 ({(班级ID, 学科): 教师ID}, [疑似教师指定但无法解析的行])。
    第二项供上层按协议 §6 写 warning（用户文字要求无法解析 → 忽略并提示）。
    支持：T001=C01语文 / T001教C01语文 / 张老师教高二1班语文
    """
    result = {}
    unparsed: List[str] = []
    if not text:
        return result, unparsed
    text = text.lstrip('\ufeff')
    name_to_tid = {t.name: tid for tid, t in teachers.items()}
    cname_to_cid = {c.name: c.id for c in classes}
    all_subjects = set(t.subject for t in teachers.values() if t.subject)
    class_ids = {c.id for c in classes}
    for line in text.splitlines():
        line = line.strip().lstrip('\ufeff')
        if not line:
            continue
        line = re.sub(r'^[指定：\s]+', '', line)
        m = re.match(r'^(.+?)\s*[=教]\s*(.+)$', line)
        if not m:
            # 只有「看起来像教师指定」（含 = 或 教）才计入未解析，
            # 否则「年级：高二」「测试用例」这类普通说明会被误报。
            if re.search(r'[=教]', line):
                unparsed.append(line)
            continue
        t_str = m.group(1).strip()
        rest = m.group(2).strip()
        tid = t_str if t_str in teachers else name_to_tid.get(t_str)
        if not tid:
            unparsed.append(line)
            continue
        cid = None
        subj = rest
        cm = re.match(r'^(C\d+)\s*(.*)$', rest)
        if cm:
            cid = cm.group(1)
            subj = cm.group(2).strip()
        else:
            for cname, cid_tmp in cname_to_cid.items():
                if rest.startswith(cname):
                    cid = cid_tmp
                    subj = rest[len(cname):].strip()
                    break
        if not cid or cid not in class_ids:
            unparsed.append(line)
            continue
        matched = None
        for s in sorted(all_subjects, key=len, reverse=True):
            if s in subj:
                matched = s
                break
        if matched:
            result[(cid, matched)] = tid
        else:
            unparsed.append(line)
    return result, unparsed


def _pick_candidates(subj, teachers_by_subject, teacher_load, k: int) -> List[str]:
    """按 (当前负载, 教师ID) 升序取前 k 位同学科教师。

    教师ID 作为并列时的兜底排序键，保证候选顺序可复现。
    """
    cands = list(teachers_by_subject.get(subj, []))
    if not cands:
        return []
    cands.sort(key=lambda t: (teacher_load.get(t, 0), t))
    return cands[:k]


def _repair_candidate_coverage(courses, teachers, k: int, teacher_load, warnings):
    """保证每位教师至少出现在 1 条课程的候选里，避免结构性闲置。

    贪心取前 K 位时，若某学科教师数 > 课程数 × K，就会有教师一次都没被选中，
    求解器无论怎么选都轮不到他（必然闲置）。这里把他补进"候选整体最忙"的那门课。
    """
    appears = {}
    for c in courses:
        for t in c.teacher_candidates:
            appears[t] = appears.get(t, 0) + 1

    k_max = k + 1
    added = []
    for tid, t in teachers.items():
        if not t.subject or tid in appears:
            continue
        pool = [c for c in courses
                if c.subject == t.subject and not c.locked
                and len(c.teacher_candidates) < k_max]
        if not pool:
            continue
        # 塞进「候选整体最忙」的那门课：给最忙的人多一个可替代选项，收益最大
        pool.sort(key=lambda c: (-sum(teacher_load.get(x, 0)
                                      for x in c.teacher_candidates), c.class_id))
        pool[0].teacher_candidates.append(tid)
        appears[tid] = 1
        added.append((tid, pool[0].class_id))

    if added:
        warnings.append(
            f"有 {len(added)} 位教师在初始候选里一次都没出现，已强制纳入候选"
            f"（避免该教师本周完全无课）")
    return added


def _generate_courses_new(classes, teachers, standards, assignments, warnings,
                          teacher_constraints=None, candidate_k: int = 3):
    std_map = {s.subject: s for s in standards}
    assignment_map = {}
    for a in assignments:
        tid = a["teacher_id"]
        cid = a["class_id"]
        subj = a["subject"]
        if not subj and tid in teachers:
            subj = teachers[tid].subject
        if subj:
            assignment_map[(cid, subj)] = tid
    if teacher_constraints:
        assignment_map.update(teacher_constraints)

    teachers_by_subject = {}
    for tid, t in teachers.items():
        if t.subject:
            teachers_by_subject.setdefault(t.subject, []).append(tid)

    subj_map = {"物": "物理", "化": "化学", "生": "生物", "政": "政治", "史": "历史", "地": "地理"}

    # ---- Pass A：先算出每条课程的课时需求（此时还不决定教师）----
    demands: List[Tuple[ClassInfo, PeriodStandard, int]] = []
    weekly_of: Dict[Tuple[str, str], int] = {}
    for ci in classes:
        elective_set = set()
        if ci.elective:
            for ch in ci.elective:
                if ch in subj_map:
                    elective_set.add(subj_map[ch])
        for std in standards:
            subj = std.subject
            weekly = std.elective_periods if subj in elective_set else std.non_elective_periods
            if weekly <= 0:
                continue
            demands.append((ci, std, weekly))
            weekly_of[(ci.id, subj)] = weekly

    # ---- Pass B：显式指派（「任课安排」sheet / 「特殊要求」）的课时先记账 ----
    # 修复：此前只在「自动分配」分支累加负载（teacher_load），显式指派的课时不计入，
    # 于是已被指派多班的教师仍被贪心当成最闲的人反复选中 → 超载 → H3 冲突 → 整表无解。
    teacher_load = {tid: 0 for tid in teachers}
    for (cid, subj), tid in assignment_map.items():
        if tid in teacher_load:
            teacher_load[tid] += weekly_of.get((cid, subj), 0)

    # ---- Pass C：逐条生成候选教师（不再"定死"一人）----
    courses: List[CourseReq] = []
    hint_assign: Dict[Tuple[str, str], str] = {}
    auto_assigned = 0
    for ci, std, weekly in demands:
        subj = std.subject
        if subj in NO_TEACHER_SUBJECTS:
            cands, locked = [], False
        else:
            tid = assignment_map.get((ci.id, subj))
            if tid:                                   # 显式指派：收缩为 1 人并锁定
                cands, locked = [tid], True
                teacher_load[tid] = teacher_load.get(tid, 0) + weekly
            else:                                     # 自动：产出 K 位有序候选
                cands = _pick_candidates(subj, teachers_by_subject,
                                         teacher_load, candidate_k)
                locked = False
                if cands:
                    # 只对【首选】记账 —— 保证 hint 与改造前的贪心结果逐条一致，
                    # 这条一致性是后面所有阶段的回归基线。
                    teacher_load[cands[0]] = teacher_load.get(cands[0], 0) + weekly
                    auto_assigned += 1
        if cands:
            hint_assign[(ci.id, subj)] = cands[0]
        courses.append(CourseReq(
            class_id=ci.id, subject=subj,
            teacher_id=(cands[0] if len(cands) == 1 else None),
            weekly=weekly, block_len=std.block_len,
            teacher_candidates=cands, locked=locked,
        ))

    # ---- Pass D：候选覆盖修复，消灭结构性闲置 ----
    _repair_candidate_coverage(courses, teachers, candidate_k, teacher_load, warnings)

    if auto_assigned > 0:
        warnings.append(
            f"有 {auto_assigned} 条课程未指定教师，已按学科与工作量各生成 "
            f"{candidate_k} 位候选教师，由排课引擎在排课的同时决定最终任课"
            f"（每门课恰好 1 位教师）。如需固定某位教师，"
            f"可在「特殊要求」中输入（格式：T001教C01语文）。"
        )
    return courses, hint_assign



def load_problem(in_dir):
    xlsx_path = None
    for fn in sorted(os.listdir(in_dir)):
        if fn.lower().startswith("input.") and fn.lower().endswith((".xlsx", ".xlsm")):
            xlsx_path = os.path.join(in_dir, fn)
            break
    if xlsx_path is None:
        raise DataError("输入目录里没有 input.xlsx")

    wb = load_workbook(xlsx_path, data_only=True, read_only=True)
    warnings_list = []

    # 格式判定放在最前面：旧格式（班级/教师/课程 3 个 sheet）已不再支持，
    # 要先明说，不要让用户看到「缺少课时标准 sheet」或「缺少班级 sheet」
    # 这种指错方向的报错。
    # 识别旧格式只能用「课程」——「课时」是「课时标准」的别名，用它判定会误判。
    period_ws = _find_sheet(wb, ["课时标准", "课时", "period_standards"])
    if period_ws is None:
        legacy = _find_sheet(wb, ["课程", "课程表", "courses"])
        wb.close()
        if legacy is not None:
            raise DataError(
                "检测到旧格式输入（班级/教师/课程），本版本已不再支持。"
                "请改用新格式：教师 / 班级 / 课时标准 / 任课安排（任课安排可选），"
                "可从软件「下载模板」获取新格式模板后重新填写。")
        raise DataError(
            "input.xlsx 缺少「课时标准」sheet。新格式需要："
            "教师 / 班级 / 课时标准（任课安排可选）。")

    classes = _load_classes(_find_sheet(wb, ["班级", "班级表", "classes"]))
    teachers = _load_teachers(_find_sheet(wb, ["教师", "教师表", "teachers"]))

    # 配置要早于课程生成：候选教师数 K 由 solver 配置决定
    cfg = Config()
    hl_path = os.path.join(in_dir, "hard_limits.json")
    if os.path.exists(hl_path):
        try:
            with open(hl_path, "r", encoding="utf-8-sig") as f:
                hl = json.load(f)
            cfg = load_from_hard_limits(hl)
        except Exception as e:
            cfg = Config()
            warnings_list.append(f"hard_limits.json 解析失败：{e}")

    # 候选教师数：关闭教师决策时强制为 1（等价预分配，全链路回退）
    candidate_k = int(getattr(cfg.solver, "teacher_candidate_k", 3) or 3)
    if not getattr(cfg.solver, "teacher_decision", True):
        candidate_k = 1
    candidate_k = max(1, min(5, candidate_k))

    # 先读 requirements —— 教师指定必须在「生成课程」时就参与负载记账，
    # 否则已指派的教师会被贪心当成最闲的人继续压课（会导致整表无解）。
    requirements = ""
    req_path = os.path.join(in_dir, "requirements.txt")
    if os.path.exists(req_path):
        try:
            with open(req_path, "r", encoding="utf-8") as f:
                requirements = f.read().strip()
        except Exception:
            requirements = ""
    teacher_constraints, unparsed_specs = _parse_teacher_constraints(
        requirements, teachers, classes)

    standards = _load_period_standards(period_ws)
    assignments = _load_teaching_assignments(
        _find_sheet(wb, ["任课安排", "任课", "teaching_assignments"]))
    if not standards:
        raise DataError("「课时标准」sheet 为空，无法排课")
    # 单次生成：教师指定一次传入，避免重复调用导致负载账本重置与告警重复
    courses, hint_assign = _generate_courses_new(
        classes, teachers, standards, assignments, warnings_list,
        teacher_constraints, candidate_k=candidate_k)
    warnings_list.append(f"新格式：{len(standards)} 门学科标准，生成 {len(courses)} 条课程")

    wb.close()

    # 统一应用教师指定：生成课程时已套用（此处幂等），
    # 但仍要保留——它承担两个用户可见职责：
    #   ① 输出「已解析 N 条、成功应用 M 条」；
    #   ② 对匹配不到的指定按协议 §6 给出 skipped 告警，避免静默丢弃。
    if teacher_constraints:
        applied, skipped = 0, []
        for (cid, subj), tid in teacher_constraints.items():
            hit = [c for c in courses if c.class_id == cid and c.subject == subj]
            if not hit:
                skipped.append(f"{tid}教{cid}{subj}")
                continue
            c = hit[0]
            # 必须收缩候选集为 1 人：只改 teacher_id 会被求解器的 y 变量覆盖
            c.teacher_candidates = [tid]
            c.teacher_id = tid
            c.locked = True
            hint_assign[(cid, subj)] = tid
            applied += 1
        warnings_list.append(
            f"已从额外约束中解析 {len(teacher_constraints)} 条教师指定，成功应用 {applied} 条。")
        if skipped:
            show = "、".join(skipped[:5]) + ("…" if len(skipped) > 5 else "")
            warnings_list.append(f"以下教师指定在课程表中找不到对应条目，已忽略：{show}")

    if unparsed_specs:
        show = "、".join(unparsed_specs[:3]) + ("…" if len(unparsed_specs) > 3 else "")
        warnings_list.append(
            f"额外约束中有 {len(unparsed_specs)} 行像是教师指定但无法解析，已忽略：{show}")

    p = Problem(classes=classes, teachers=teachers, courses=courses,
                requirements=requirements, config=cfg, warnings=warnings_list,
                hint_assign=hint_assign)
    _post_process(p)
    return p


def _post_process(p):
    cfg = p.config
    grid = cfg.grid_size()

    if cfg.consecutive.only_core_subjects:
        core = set(cfg.consecutive.subjects)
        for c in p.courses:
            if c.subject not in core and c.block_len > 0:
                p.warnings.append(f"班级 {c.class_id} 的「{c.subject}」连堂已自动取消")
                c.block_len = 0

    if cfg.solver.self_study_fill:
        for ci in p.classes:
            has_ss = any(c.subject == "自习" and c.class_id == ci.id for c in p.courses)
            if has_ss:
                continue
            used = sum(c.weekly for c in p.courses if c.class_id == ci.id)
            rest = grid - used
            if rest > 0:
                p.courses.append(CourseReq(
                    class_id=ci.id, subject="自习", teacher_id=None,
                    weekly=rest, block_len=0))
