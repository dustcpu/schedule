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
# （「信息」「心理」「体育」「艺术」是课表上用的**标准显示名**，用户 2026-10-01 确认）
NO_TEACHER_SUBJECTS = {"体育", "体育活动", "艺术", "音乐", "美术", "信息", "信息技术", "通用技术", "心理", "班会", "研究性学习", "校本课", "自习", "书法", "劳动", "生涯规划"}

# 问题1（2026-10-01 实测）：学校填的学科名写法五花八门（信息课/信息/信息技术…），
# 白名单是精确字符串匹配，漏一个别名就把整套排课卡死在阶段 2。
# 与其穷举别名，不如在读取入口统一归一化到**课表上要显示的那个标准名**。
#
# 标准名由用户拍板：课表上写「信息」「心理」「体育」「艺术」即可（短、好排版）。
_SUBJECT_ALIASES = {
    # → 信息（原写法是「信息技术」，2026-10-01 按用户要求改成「信息」）
    "信息技术": "信息", "信息课": "信息", "信息技术课": "信息",
    "计算机": "信息", "电脑": "信息", "计算机课": "信息",
    "心理健康": "心理", "心理课": "心理", "心理健康课": "心理",
    "体育课": "体育", "体育与健康": "体育",
    "美术课": "美术", "音乐课": "音乐", "艺术课": "艺术",
    "班会课": "班会", "自习课": "自习",
    "劳动课": "劳动", "劳技": "劳动", "劳动技术": "劳动",
    "书法课": "书法",
    "生涯规划课": "生涯规划",
    "通用技术课": "通用技术",
    "研究性学习课": "研究性学习",
    "校本课程": "校本课",
}


def normalize_subject(name: str) -> str:
    """学科名归一化：常见别名/口语写法 → 标准名；未知名原样返回。"""
    if not name:
        return name
    n = str(name).strip()
    if not n:
        return n
    if n in _SUBJECT_ALIASES:
        return _SUBJECT_ALIASES[n]
    return n
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Tuple

from openpyxl import load_workbook

from ..config import (
    Config, SELF_STUDY, _norm_header, NUM_DAYS,
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
    # 「班级」sheet 的「班主任」列（教师ID）。2026-10-05 之前这一列**没被读取**，
    # 设置页的「班主任须在本班任课」开关要靠它。可能为空（没填）或写的是姓名。
    homeroom: str = ""


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
class StructuredConstraint:
    """结构化额外约束（约束转换器产出，schema v1，见 docs/约束能力清单.md）。

    与「教师指定」（文本解析）互补：教师指定走 CourseReq.locked 语义，
    其余四类在此承载，由 solver/model.py 落成 CP-SAT 约束。
    days: 1-5（周一~周五）；periods: 1..periods_per_day。
    """
    type: str                       # teacher_unavailable / teacher_no_double_day /
                                    # class_unavailable / teacher_max_classes
    teacher: str = ""
    class_id: str = ""
    subject: str = ""
    days: List[int] = field(default_factory=list)
    periods: List[int] = field(default_factory=list)   # 空 = 全天
    max_classes: int = 0
    raw: Dict[str, Any] = field(default_factory=dict)  # 原始 JSON（回显/校验用）

    def describe(self, problem: "Problem") -> str:
        """「我理解成……」人话口径（与约束转换器回显一致）。"""
        t = self.type
        tid_show = problem.teacher_name(self.teacher) + f"（{self.teacher}）" \
            if self.teacher else ""
        days_txt = "、周".join("一二三四五"[d - 1] for d in self.days)
        if t == "teacher_unavailable":
            slot = ""
            if self.periods:
                if self.periods == list(range(1, problem.config.schedule.morning_periods + 1)):
                    slot = "上午 "
                elif self.periods[-1] == problem.config.schedule.periods_per_day \
                        and self.periods[0] == problem.config.schedule.morning_periods + 1:
                    slot = "下午 "
                elif len(self.periods) == 1:
                    slot = f"第{self.periods[0]}节 "
                else:
                    slot = f"第{self.periods[0]}-{self.periods[-1]}节 "
            return f"{tid_show} 周{days_txt} {slot}不排课"
        if t == "teacher_no_double_day":
            return f"{tid_show} 周{days_txt} 不排连堂"
        if t == "class_unavailable":
            d = self.days[0] if self.days else 0
            slot = ""
            if self.periods:
                if self.periods == list(range(1, problem.config.schedule.morning_periods + 1)):
                    slot = "上午 "
                elif self.periods[-1] == problem.config.schedule.periods_per_day \
                        and self.periods[0] == problem.config.schedule.morning_periods + 1:
                    slot = "下午 "
                elif len(self.periods) == 1:
                    slot = f"第{self.periods[0]}节 "
                else:
                    slot = f"第{self.periods[0]}-{self.periods[-1]}节 "
            return f"班级 {self.class_id} 周{'一二三四五'[d - 1] if d else '?'} {slot}不排课" \
                   f"（这些格将显示为自习）"
        if t == "teacher_max_classes":
            return f"{tid_show} 最多带 {self.max_classes} 个班"
        return str(self.raw)


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
    # 结构化额外约束（structured_requirements.json / requirements.txt 围栏块）
    structured: List[StructuredConstraint] = field(default_factory=list)

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
            # 班主任列可能写 ID 也可能写姓名（教师表此时还没读，先原样存，
            # 留到 apply_homeroom_own_class 里用 _resolve_id 做容错解析）。
            homeroom=_cell_str(r.get("班主任")),
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
            subject=normalize_subject(_cell_str(r.get("任教学科") or r.get("学科"))),
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
            subject=normalize_subject(subj),
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
            "subject": normalize_subject(_cell_str(r.get("学科") or r.get("科目"))),
        })
    return out


def _id_key(s):
    """把 T030 / t30 / T30 归一成同一个键（前缀大写 + 数字去前导零）。"""
    m = re.match(r'^([A-Za-z]*)(\d+)$', str(s).strip())
    return (m.group(1).upper(), m.group(2).lstrip('0')) if m else None


def _ids_matching(token, ids):
    """和 token 只差前导零的那些 ID（T030 → T30）。"""
    key = _id_key(token)
    if key is None:
        return []
    return [i for i in ids if _id_key(i) == key]


def _resolve_id(token, ids):
    """ID 查找：先精确匹配，再容忍"补零/去零"的写法。

    学校的教师/班级编号常见 T30 与 T030 混写。用户照文档写 T030，而表里是 T30，
    整条特殊要求就被忽略了（2026-10-01 实测）。这里只在**唯一匹配**时才接受，
    避免把 T3 误认成 T30 或 T31。
    """
    if token in ids:
        return token
    hits = _ids_matching(token, ids)
    return hits[0] if len(hits) == 1 else None


def _explain_unparsed(line, teachers, classes):
    """给"解析不了"的一行找一句"该怎么办"的话；确定不了就返回 None。

    只报"无法解析"对用户没有帮助 —— 他明明照着文档写了（2026-10-01 实测）。
    """
    m = re.match(r'^(.+?)\s*[=教]\s*(.+)$', line)
    if not m:
        return None
    t_str = m.group(1).strip()
    rest = m.group(2).strip()

    known_names = {t.name for t in teachers.values()}
    if t_str not in teachers and t_str not in known_names and _resolve_id(t_str, list(teachers)) is None:
        near = _ids_matching(t_str, list(teachers))
        tip = f"，表里有「{near[0]}」，是不是想写它？" if near else ""
        return (f"「{t_str}」不在「教师」表里{tip}。"
                f"教师ID 要照抄「教师」表里的写法（别自己补零或去零）")

    # 教师能对上 → 问题多半出在班级
    class_ids = [c.id for c in classes]
    cm = re.match(r'^(C\d+)', rest)
    if cm:
        cid = cm.group(1)
    else:
        cid = next((c.id for c in classes if rest.startswith(c.name or c.id)), None)
    if cid is None or _resolve_id(cid, class_ids) is None:
        near = _ids_matching(rest[:4], class_ids)
        tip = f"（表里有「{near[0]}」）" if near else ""
        return (f"没能从「{rest}」里认出班级ID{tip}。"
                f"写法是 教师ID教班级ID学科（例：{t_str}教{class_ids[0] if class_ids else 'C01'}语文）")
    return None


def _parse_teacher_constraints(text, teachers, classes):
    """从额外约束文本中解析教师指定。

    返回 ({(班级ID, 学科): 教师ID}, [疑似教师指定但无法解析的行])。
    第二项供上层按协议 §6 写 warning（用户文字要求无法解析 → 忽略并提示）。
    支持：T001=C01语文 / T001教C01语文 / 张老师教高二1班语文
    """
    result = {}
    unparsed: List[str] = []
    unsupported: List[str] = []
    if not text:
        return result, unparsed, unsupported
    text = text.lstrip('\ufeff')
    name_to_tid = {t.name: tid for tid, t in teachers.items()}
    cname_to_cid = {c.name: c.id for c in classes}
    all_subjects = set(t.subject for t in teachers.values() if t.subject)
    class_ids = {c.id for c in classes}
    # 2a: 用户习惯像记笔记一样写「1.」「1、」「（1）」「①」「- 」等列表标记，
    # 不剥掉的话「1.T30教C05数学」里第一个「教」之前是「1.T30」，匹配不到教师 → 整行丢弃。
    #
    # ⚠️ 数字后面**必须跟标点**才算编号。早先的写法把标点设成可选，
    #    于是「2026年秋季作息时间」「3月1日开始执行」这种以数字开头的普通文字
    #    也被当成编号剥掉，还被下面的 2c 误报成"一条没生效的约束"（2026-10-01 实测）。
    list_mark_re = re.compile(
        r'^\s*(?:'
        r'\d{1,3}\s*[.、)）:：]'          # 1. / 1、/ 1) / 1：
        r'|[（(]\s*\d{1,3}\s*[)）]'       # （1） / (1)
        r'|[-*•]'                          # - / * / •
        r'|[①-⑳]'                         # ① ② …
        r')\s*'
    )
    # 2c 补全（2026-10-03 用户实测）：上面那两条判定都够不着**用自然语言写的要求**。
    #   用户写「T48不想周五上课」——既不含 教/=，也没写编号 → 三条都不命中 → 静默消失。
    #   用户以为生效了、实际没有，这是最糟的一种失败（比直接报错还糟）。
    #   这里补一条启发式：行里出现"星期词"或"要求动词 / 连堂词 / 数量限制词"，
    #   就当作一条要求，归入"当前不支持"并给出改写指引。**宁可多说一句，也不要静默。**
    #   （普通说明如「年级：高二」「2026年秋季作息」不含这些词，不会被误报。）
    day_word_re = re.compile(r'周[一二三四五六日天]|星期[一二三四五六日天]')
    req_hint_re = re.compile(
        r'不排|不上|没空|不空|请假|有事|外出|教研|调休|不能|禁止|避免|不要|别排|'
        r'连堂|连排|连着|双节|最多|最少|至少|不超过|上限|下限'
    )
    for line in text.splitlines():
        raw = line
        line = line.strip().lstrip('\ufeff')
        if not line:
            continue
        line = re.sub(r'^[指定：\s]+', '', line)
        line = list_mark_re.sub('', line)
        if not line:
            continue
        m = re.match(r'^(.+?)\s*[=教]\s*(.+)$', line)
        if not m:
            # 2c: 不再静默丢弃。区分两类未生效：
            #   含「教/=」→ 像教师指定但语法看不懂；以编号/列表标记开头 → 明显在写约束清单，
            #   但要求类型（如"某班每周加一节 X"）当前不支持。普通说明（如「年级：高二」）不打扰。
            if re.search(r'[=教]', line):
                unparsed.append(line)
            elif (list_mark_re.match(raw.strip())
                  or day_word_re.search(line)
                  or req_hint_re.search(line)):
                unsupported.append(raw.strip())
            continue
        t_str = m.group(1).strip()
        rest = m.group(2).strip()
        tid = t_str if t_str in teachers else name_to_tid.get(t_str)
        if not tid:
            # 容忍 T030 ↔ T30 这类"补零/去零"的写法差异
            tid = _resolve_id(t_str, list(teachers))
        if not tid:
            # 收严（2026-10-03）：上面那个含"教"的正则会把「本表由教务处维护」
            # 也切成「本表由教务 / 务维护」两段，左半段当然不是教师 —— 但把它报成
            # "像是教师指定"只会让人困惑。只在左半段**确实像教师**时才这么报：
            # 是 T30 / C05 这样的 ID，或"张三 / 张三老师"这样的姓名形状。
            looks_like_teacher = bool(
                re.search(r'[TCtc]\d{1,4}', t_str)
                or re.fullmatch(r'[\u4e00-\u9fa5]{2,4}老师?', t_str)
            )
            if looks_like_teacher or '=' in line:
                unparsed.append(line)
            elif day_word_re.search(line) or req_hint_re.search(line):
                unsupported.append(raw.strip())
            continue
        cid = None
        subj = normalize_subject(rest)
        cm = re.match(r'^(C\d+)\s*(.*)$', rest)
        if cm:
            cid = cm.group(1)
            subj = cm.group(2).strip()
        else:
            for cname, cid_tmp in cname_to_cid.items():
                if rest.startswith(cname):
                    cid = cid_tmp
                    subj = normalize_subject(rest[len(cname):].strip())
                    break
        if cid and cid not in class_ids:
            cid = _resolve_id(cid, list(class_ids))     # 同样容忍 C01 ↔ C1
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
    return result, unparsed, unsupported


def _extract_fenced_json(text: str):
    """提取文本里的 ```json ... ``` 围栏块（裸 ``` 也认）。

    返回 (解析结果列表, 删掉围栏块后的文本)。围栏块是「约束转换器」的粘贴通道：
    不删掉的话，JSON 的每一行都会落进"不支持"告警里变成噪音。
    """
    blocks: List[str] = []

    def _grab(m):
        blocks.append(m.group(1))
        return ""

    cleaned = re.sub(r"```(?:json)?\s*\n?(\{.*?\})\s*\n?```", _grab, text or "",
                     flags=re.S)
    parsed = []
    for b in blocks:
        try:
            parsed.append(json.loads(b))
        except Exception:
            parsed.append(b)   # 解析失败的原文，交给上层告警
    return parsed, cleaned


_STRUCTURED_TYPES = ("assign_teacher", "teacher_unavailable", "teacher_no_double_day",
                     "class_unavailable", "teacher_max_classes")


def _validate_structured_item(item, teachers, class_ids, cfg):
    """校验一条结构化约束，合法返回 (type, 规整字段)，不合法返回 (None, 原因)。

    teachers: {教师ID: TeacherInfo}；class_ids: {班级ID}（集合）。
    """
    if not isinstance(item, dict):
        return None, "不是对象（应为 {\"type\": ...}）"
    t = item.get("type")
    if t not in _STRUCTURED_TYPES:
        return None, (f"未知类型「{t}」。只支持：{'、'.join(_STRUCTURED_TYPES)}"
                      f"（宁可弃权，不要猜）")

    def _tid_ok(token):
        if token in teachers:
            return token
        return _resolve_id(str(token), list(teachers))

    def _days(raw):
        if raw is None:
            return None, "缺少 days（1-5，周一到周五）"
        if isinstance(raw, int):
            raw = [raw]
        if not isinstance(raw, list) or not raw:
            return None, "days 应为非空列表（如 [2] 或 [1,3]）"
        out = []
        for d in raw:
            if not isinstance(d, int) or isinstance(d, bool) or not (1 <= d <= NUM_DAYS):
                return None, f"days 里的「{d}」不是 1-{NUM_DAYS} 的整数"
            if d not in out:
                out.append(d)
        return out, ""

    def _periods(raw):
        if raw in (None, [], ""):
            return [], ""       # 空 = 全天
        if isinstance(raw, int):
            raw = [raw]
        if not isinstance(raw, list):
            return None, "periods 应为节次列表（如 [6,7,8]）"
        per_day = cfg.schedule.periods_per_day
        out = []
        for p in raw:
            if not isinstance(p, int) or isinstance(p, bool) or not (1 <= p <= per_day):
                return None, f"periods 里的「{p}」超出 1-{per_day} 节"
            if p not in out:
                out.append(p)
        return sorted(out), ""

    if t == "assign_teacher":
        tid = _tid_ok(item.get("teacher"))
        if not tid:
            return None, f"教师「{item.get('teacher')}」不在「教师」表里"
        cid = item.get("class")
        if cid not in class_ids:
            cid2 = _resolve_id(str(cid), list(classes))
            if not cid2:
                return None, f"班级「{cid}」不在「班级」表里"
            cid = cid2
        subj = normalize_subject(str(item.get("subject") or "").strip())
        if not subj:
            return None, "缺少 subject"
        return ("assign_teacher", {"teacher": tid, "class": cid, "subject": subj})

    if t in ("teacher_unavailable", "teacher_no_double_day"):
        tid = _tid_ok(item.get("teacher"))
        if not tid:
            return None, f"教师「{item.get('teacher')}」不在「教师」表里"
        days, err = _days(item.get("days"))
        if err:
            return None, err
        if t == "teacher_unavailable":
            periods, err = _periods(item.get("periods"))
            if err:
                return None, err
            return (t, {"teacher": tid, "days": days, "periods": periods})
        return (t, {"teacher": tid, "days": days})

    if t == "class_unavailable":
        cid = item.get("class")
        if cid not in class_ids:
            cid2 = _resolve_id(str(cid), list(classes))
            if not cid2:
                return None, f"班级「{cid}」不在「班级」表里"
            cid = cid2
        day = item.get("day")
        if isinstance(day, list):
            return None, "day 应为单个整数（一天一条，多天请拆成多条）"
        days, err = _days([day] if day is not None else None)
        if err:
            return None, "day " + err
        periods, err = _periods(item.get("periods"))
        if err:
            return None, err
        return (t, {"class": cid, "days": days, "periods": periods})

    # teacher_max_classes
    mx = item.get("max")
    tid = _tid_ok(item.get("teacher"))
    if not tid:
        return None, f"教师「{item.get('teacher')}」不在「教师」表里"
    if not isinstance(mx, int) or isinstance(mx, bool) or mx < 1:
        return None, f"max「{mx}」应为 ≥1 的整数"
    return (t, {"teacher": tid, "max_classes": mx})


def load_structured_constraints(in_dir, requirements_text, teachers, classes, cfg,
                                warnings):
    """读取结构化约束（双通道）：
      ① 输入目录可选文件 structured_requirements.json（约束转换器 --out 产出）；
      ② requirements.txt 里的 ```json 围栏块（转换器产出，可粘贴进「特殊要求」框）。

    返回 (constraints, assign_overrides, 清理后的 requirements 文本)。
    每条独立校验，不合法只丢那条并给出原因（协议 §8：无法解析 → 忽略 + 告警）。
    """
    constraints: List[StructuredConstraint] = []
    assign_overrides: Dict[Tuple[str, str], str] = {}
    sources: List[Tuple[str, Any]] = []
    class_ids = {c.id for c in classes}

    fenced, cleaned = _extract_fenced_json(requirements_text)
    for i, data in enumerate(fenced, 1):
        sources.append((f"requirements.txt 围栏块{i}", data))
    path = os.path.join(in_dir, "structured_requirements.json")
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                sources.append(("structured_requirements.json", json.load(f)))
        except Exception as e:
            warnings.append(f"structured_requirements.json 解析失败，已忽略：{e}")

    for label, data in sources:
        if isinstance(data, str):
            warnings.append(f"{label}不是合法 JSON，已忽略（AI 返回的内容要存成纯 JSON）")
            continue
        if not isinstance(data, dict):
            warnings.append(f"{label}格式不对（应为 {{\"version\":…, \"constraints\":[…]}}），已忽略")
            continue
        version = data.get("version", 1)
        if version != 1:
            warnings.append(f"{label}的 version={version}，本引擎只认 v1，该块已忽略")
            continue
        items = data.get("constraints")
        if items is None:
            continue
        if not isinstance(items, list):
            warnings.append(f"{label}的 constraints 不是列表，已忽略")
            continue
        for idx, item in enumerate(items, 1):
            t, fields = _validate_structured_item(item, teachers, class_ids, cfg)
            if t is None:
                show = json.dumps(item, ensure_ascii=False)
                show = show if len(show) <= 60 else show[:60] + "…"
                warnings.append(f"结构化约束第{idx}条未生效：{fields}（{show}）")
                continue
            if t == "assign_teacher":
                key = (fields["class"], fields["subject"])
                if key in assign_overrides and assign_overrides[key] != fields["teacher"]:
                    warnings.append(
                        f"{label}第{idx}条与更早的指定冲突"
                        f"（{key[0]}{key[1]}已指定给 {assign_overrides[key]}），"
                        f"以本条为准")
                assign_overrides[key] = fields["teacher"]
                continue
            constraints.append(StructuredConstraint(
                type=t, teacher=fields.get("teacher", ""),
                class_id=fields.get("class", ""),
                days=fields.get("days", []),
                periods=fields.get("periods", []),
                max_classes=fields.get("max_classes", 0),
                raw=dict(item),
            ))

    # 跨通道去重（同一约束写两遍只生效一次）
    seen, uniq = set(), []
    for c in constraints:
        key = json.dumps({"type": c.type, "teacher": c.teacher,
                          "class": c.class_id, "days": c.days,
                          "periods": c.periods, "max": c.max_classes},
                         sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(c)
    return uniq, assign_overrides, cleaned


def apply_structured_double_bans(courses, structured, teachers, cfg, warnings,
                                 hint_assign):
    """教师连堂禁日 → 候选收缩：连堂日按学科固定，禁该日的连堂 = 禁整科任课。

    直接把该教师移出相关课程的候选集——H26 的下界（lo=1 对称性破除）、贪心
    hint 与求解空间随之自然收缩；若改在模型层用 b+y≤1 表达，会与 H26 的
    lo=1 相撞导致假性无解（2026-10-03 小模型实测）。
    """
    for sc in structured or []:
        if sc.type != "teacher_no_double_day":
            continue
        t_name = teachers[sc.teacher].name if sc.teacher in teachers else sc.teacher
        d_name = "、周".join("一二三四五"[d - 1] for d in sc.days)
        removed: List[str] = []
        emptied: Optional[CourseReq] = None
        emptied_day = 0
        for c in courses:
            if (c.subject in NO_TEACHER_SUBJECTS or c.block_len <= 1
                    or sc.teacher not in (c.teacher_candidates or [])):
                continue
            dday = cfg.consecutive.day_of(c.subject)
            if dday is None or dday not in sc.days:
                continue
            c.teacher_candidates = [t for t in c.teacher_candidates if t != sc.teacher]
            if not c.teacher_candidates:
                emptied = c
                emptied_day = dday
                break
            c.teacher_id = (c.teacher_candidates[0]
                            if len(c.teacher_candidates) == 1 else None)
            hint_assign[(c.class_id, c.subject)] = c.teacher_candidates[0]
            removed.append(f"{c.class_id}「{c.subject}」")
        if emptied is not None:
            d = "一二三四五"[emptied_day - 1]
            if emptied.locked:
                raise DataError(
                    f"结构化约束冲突：{t_name}（{sc.teacher}）不想周{d}上连堂，"
                    f"但该学科连堂固定在周{d}，而 {emptied.class_id} 的"
                    f"「{emptied.subject}」已显式指定给他任教，两条要求无法同时满足。"
                    f"请取消其中一条")
            raise DataError(
                f"班级 {emptied.class_id} 的「{emptied.subject}」所有候选教师都要求"
                f"周{d}不排连堂，而该学科连堂固定在周{d}，无人可任课。"
                f"请调整结构化约束（约束转换器对这类冲突有预警），或为该学科增加教师")
        if removed:
            show = "、".join(removed[:4]) + ("…" if len(removed) > 4 else "")
            warnings.append(
                f"结构化约束生效：{t_name}（{sc.teacher}）周{d_name}不排连堂，"
                f"已将其移出 {len(removed)} 门课程的候选：{show}")


def apply_homeroom_own_class(courses, classes, teachers, cfg, warnings):
    """H29 班主任须在本班任课：把班主任补进「本班 + 他任教学科」那门课的候选集。

    由设置页「班主任必须在自己担任班主任的班上任课」开关控制（cfg.homeroom_must_teach_own），
    **默认关**，关着时行为与旧版完全一致。

    这里只负责"让 y 变量存在"——"至少 1 节"由建模层写 sum(y[...]) >= 1。
    班主任若不在候选集里，模型里就没有对应的 0/1 变量，那条约束无从表达。

    顺带把 ClassInfo.homeroom 归一成教师ID（表里那列可能写的是姓名），
    供建模层与 verify_hard 直接使用；解析不了的置空（上面已给出具体原因）。
    """
    if not getattr(cfg, "homeroom_must_teach_own", False):
        return
    by_key = {(c.class_id, c.subject): c for c in courses}
    added = 0
    no_teacher, no_subject, no_course, locked = [], [], [], []
    for ci in classes:
        raw = (getattr(ci, "homeroom", "") or "").strip()
        if not raw:
            continue
        tid = raw if raw in teachers else _resolve_id(raw, list(teachers))
        if not tid:                       # 表里那列写的是姓名的情况
            tid = next((t.id for t in teachers.values() if t.name == raw), None)
        ci.homeroom = tid or ""           # 归一；解析不了就置空，避免下游重复告警
        if not tid:
            no_teacher.append(f"{ci.id}（{raw}）")
            continue
        subj = (teachers[tid].subject or "").strip()
        if not subj:
            no_subject.append(f"{ci.id}（{tid} 未填任教学科）")
            continue
        c = by_key.get((ci.id, subj))
        if c is None or subj in NO_TEACHER_SUBJECTS:
            no_course.append(f"{ci.id} 的「{subj}」")
            continue
        if getattr(c, "locked", False):
            # 该课已被「特殊要求」显式指定给别人 —— 用户意图优先，不覆盖
            if c.teacher_id != tid:
                locked.append(f"{ci.id}（{subj}已指定给 {c.teacher_id}）")
            continue
        cands = list(c.teacher_candidates or [])
        if tid not in cands:
            cands.append(tid)
            c.teacher_candidates = cands
            added += 1
    if added:
        warnings.append(
            f"班主任必须教自己带的班：已把 {added} 位班主任补进其本班课程的候选")
    for items, why in (
        (no_teacher, "「班主任」在教师表里找不到"),
        (no_subject, "班主任没有任教学科"),
        (no_course, "班主任所任教的学科在其本班没有课时"),
        (locked, "对应课程已被显式指定给别的教师"),
    ):
        if items:
            show = "、".join(items[:5]) + ("…" if len(items) > 5 else "")
            warnings.append(f"「班主任必须教自己带的班」对以下班级无法生效（{why}）：{show}")


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
    # 目录不存在 / 读不到：转成 DataError，给出教务老师看得懂的提示。
    # 否则会抛 FileNotFoundError 走通用崩溃路径，用户只看到一句"引擎内部出错"。
    try:
        entries = sorted(os.listdir(in_dir))
    except OSError as e:
        # Windows 的 strerror 自带句号，去掉免得变成「路径。。」
        reason = (e.strerror or str(e)).rstrip("。. ")
        raise DataError(
            f"读不到输入目录「{in_dir}」：{reason}。"
            f"请确认该目录存在，且里面放着 input.xlsx。"
        ) from e

    xlsx_path = None
    for fn in entries:
        if fn.lower().startswith("input.") and fn.lower().endswith((".xlsx", ".xlsm")):
            xlsx_path = os.path.join(in_dir, fn)
            break
    if xlsx_path is None:
        raise DataError("输入目录里没有 input.xlsx")

    try:
        wb = load_workbook(xlsx_path, data_only=True, read_only=True)
    except Exception as e:
        # 常见于：文件正被 Excel 打开占用、或者文件已损坏
        raise DataError(
            f"打不开「{os.path.basename(xlsx_path)}」：{e}。"
            f"请先关闭正在打开该文件的 Excel，或换一个没被占用的文件。"
        ) from e
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
    # 结构化约束（双通道）：① 输入目录可选文件 structured_requirements.json
    # ② requirements.txt 里的 ```json 围栏块（约束转换器产出）。
    # 必须在解析教师指定之前做——围栏块要从文本里剥掉，assign 类要并入指定任课。
    structured_reqs, assign_struct, requirements = load_structured_constraints(
        in_dir, requirements, teachers, classes, cfg, warnings_list)
    teacher_constraints, unparsed_specs, unsupported_specs = _parse_teacher_constraints(
        requirements, teachers, classes)
    for key, tid in assign_struct.items():
        if key in teacher_constraints and teacher_constraints[key] != tid:
            warnings_list.append(
                f"同一门课（{key[0]} {key[1]}）在文本与结构化约束里指定了不同教师"
                f"（{teacher_constraints[key]} / {tid}），以结构化为准")
        teacher_constraints[key] = tid

    standards = _load_period_standards(period_ws)
    assignments = _load_teaching_assignments(
        _find_sheet(wb, ["任课安排", "任课", "teaching_assignments"]))
    if not standards:
        raise DataError("「课时标准」sheet 为空，无法排课")

    # N1（2026-10-01 实测）：「选考周课时」列只对出现在班级选科组合里的学科生效；
    # 语数英等必考学科不在任何选科组合里 → 恒用「非选考周课时」。选考≠非选考时静默不生效，
    # 教务改了数字以为改成功了。这里显式提醒。
    _subj_map = {"物": "物理", "化": "化学", "生": "生物", "政": "政治", "史": "历史", "地": "地理"}
    used_elective_subjects = set()
    for ci in classes:
        for ch in (ci.elective or ""):
            if ch in _subj_map:
                used_elective_subjects.add(_subj_map[ch])
    n1_warned = False
    for std in standards:
        if (not n1_warned and std.subject not in used_elective_subjects
                and std.elective_periods != std.non_elective_periods
                and std.elective_periods > 0):
            warnings_list.append(
                f"学科「{std.subject}」不在任何班级的选科组合中，「选考周课时」({std.elective_periods}) 不会生效，"
                f"实际按「非选考周课时」({std.non_elective_periods}) 排课；如需调整请改「非选考周课时」列。")
            n1_warned = True  # 同类提醒只发一次，避免刷屏

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

    # H29 班主任须在本班任课（设置页开关，默认关）。
    # 必须放在"教师指定"之后：被显式指定给别人的课不能覆盖，这里只补候选。
    apply_homeroom_own_class(courses, classes, teachers, cfg, warnings_list)

    # 结构化约束·教师连堂禁日：见 apply_structured_double_bans（独立成函数，
    # 供手工构造 Problem 的测试复用同一条收缩逻辑）。
    apply_structured_double_bans(courses, structured_reqs, teachers, cfg,
                                 warnings_list, hint_assign)

    if unparsed_specs:
        # 尽量说清"该怎么办"，而不是只丢一句"无法解析"：
        # 用户 2026-10-01 照着书写规范写，结果被忽略，且看不出哪里写错了。
        explained = 0
        for line in unparsed_specs:
            if explained >= 3:
                break
            why = _explain_unparsed(line, teachers, classes)
            if why:
                warnings_list.append(f"「{line}」没生效：{why}")
                explained += 1
        if explained == 0:
            show = "、".join(unparsed_specs[:3]) + ("…" if len(unparsed_specs) > 3 else "")
            warnings_list.append(
                f"额外约束中有 {len(unparsed_specs)} 行像是教师指定但无法解析，已忽略：{show}"
                f"（正确写法：教师ID教班级ID学科，例如 T30教C05英语）")
        if len(unparsed_specs) > explained:
            warnings_list.append(
                f"另有 {len(unparsed_specs) - explained} 行教师指定没能解析，已忽略。")

    if unsupported_specs:
        # 用户 2026-10-03 实测：写「T48不想周五上课」时这行被静默丢弃，用户只能靠
        # "结果好像少了点什么"去猜。这里补上告警，并把"该改成什么"一次说清。
        show = "；".join(unsupported_specs[:3]) + ("…" if len(unsupported_specs) > 3 else "")
        warnings_list.append(
            f"额外约束中有 {len(unsupported_specs)} 行没有生效：{show}。"
            f"目前只认两种写法——①「教师ID教班级ID学科」（如 T001教C01语文）；"
            f"② JSON 结构化约束（把一段 JSON 整段粘进「特殊要求」，格式见界面「书写规范」）。"
            f"这类自然语言可先用仓库里的离线转换器（约束转换器/nl2req.py）转成 JSON 再用。")

    if structured_reqs:
        _kind_names = {"teacher_unavailable": "教师不可用",
                       "teacher_no_double_day": "教师连堂禁日",
                       "class_unavailable": "班级不可用",
                       "teacher_max_classes": "教师带班数上限"}
        kinds: Dict[str, int] = {}
        for c in structured_reqs:
            kinds[c.type] = kinds.get(c.type, 0) + 1
        detail = "、".join(f"{_kind_names[k]} {n} 条" for k, n in sorted(kinds.items()))
        warnings_list.append(
            f"已接收 {len(structured_reqs)} 条结构化约束（{detail}），"
            f"建模时生效并逐条回显；校验器会对其复核")

    p = Problem(classes=classes, teachers=teachers, courses=courses,
                requirements=requirements, config=cfg, warnings=warnings_list,
                hint_assign=hint_assign, structured=structured_reqs)
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
