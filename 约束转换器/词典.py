# -*- coding: utf-8 -*-
"""规则词典：把教务的自然语言一行解析成结构化约束（离线，不联网、不调用模型）。

依据《额外约束处理方案评估》定案（2026-10-03）：主线走结构化输入 + 规则词典。
设计原则：
  - 认不出就明确说认不出（弃权），绝不硬猜；
  - 命中后给出「我理解成……」回显，供人工确认后才交给引擎；
  - 词典能力范围 = 引擎已支持的约束类型（schema v1），两者同步扩展。

本模块只依赖标准库；教师/班级姓名解析由 nl2req.py 注入的 ParseContext 完成。
"""
import re

SCHEMA_VERSION = 1

# 引擎当前支持的结构化约束类型（schema v1，与 docs/约束能力清单.md 同步）
CONSTRAINT_TYPES = (
    "assign_teacher",
    "teacher_unavailable",
    "teacher_no_double_day",
    "class_unavailable",
    "teacher_max_classes",
)

# ---- 文本归一化 ---------------------------------------------------------------

_CN_NUM = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}

# 行首列表标记（与引擎 _parse_teacher_constraints 的口径一致）
_LIST_MARK_RE = re.compile(
    r'^\s*(?:'
    r'\d{1,3}\s*[.、)）:：]'
    r'|[（(]\s*\d{1,3}\s*[)）]'
    r'|[-*•]'
    r'|[①-⑳]'
    r')\s*'
)

_WEEK_RE = re.compile(r'(?:周|星期|礼拜)\s*([一二三四五1-5])')
# 第N节 / 第N-M节（N、M 支持汉字数字）
_RANGE_RE = re.compile(
    r'第\s*([0-9一二三四五十]+)\s*(?:[-—–~至到]\s*([0-9一二三四五十]+))?\s*节')

_TRAILING = "。．.，,！!？?；;：: "


def cn_num(s):
    """'12' / '十二' → int；认不出返回 None。"""
    s = str(s).strip()
    if s.isdigit():
        return int(s)
    if s == "十":
        return 10
    if "十" in s:
        a, _, b = s.partition("十")
        tens = _CN_NUM.get(a, 1) if a else 1
        ones = _CN_NUM.get(b, 0) if b else 0
        return tens * 10 + ones
    return _CN_NUM.get(s)


def clean_line(raw: str) -> str:
    """去掉行首编号、首尾标点，全角字母数字转半角。"""
    line = (raw or "").strip().lstrip("\ufeff")
    line = _LIST_MARK_RE.sub("", line)
    line = line.translate(str.maketrans(
        "０１２３４５６７８９ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ"
        "ａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ（）",
        "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        "abcdefghijklmnopqrstuvwxyz()"))
    return line.strip(_TRAILING)


def extract_days(line: str):
    """行里出现的星期（1-5，可多个，保持出现顺序、去重）。"""
    out = []
    for m in _WEEK_RE.finditer(line):
        d = cn_num(m.group(1))
        if d and d not in out:
            out.append(d)
    return out


def extract_periods(line: str, morning: int, per_day: int):
    """行里的节次/时段 → 节次列表（如 [7,8]）；没提节次返回 []。

    上午 = 1..morning，下午 = morning+1..per_day（与引擎 ScheduleRule 一致）。
    """
    out = []
    for m in _RANGE_RE.finditer(line):
        a = cn_num(m.group(1))
        b = cn_num(m.group(2)) if m.group(2) else a
        if a is None or b is None:
            continue
        out.extend(p for p in range(min(a, b), max(a, b) + 1))
    if out:
        return sorted(set(p for p in out if 1 <= p <= per_day))
    if "上午" in line:
        return list(range(1, morning + 1))
    if "下午" in line:
        return list(range(morning + 1, per_day + 1))
    return []


# ---- 关键词表 ----------------------------------------------------------------

# 连堂禁日的触发词：既要出现"连堂"类词，又要有否定/回避语气
DOUBLE_KW = ("连堂", "连排", "连着", "两节连", "双节")
NEG_KW = ("不想", "不要", "别", "避免", "避开", "禁止", "不安排", "不来", "不希望")

# 教师带班数上限
_MAX_RE = re.compile(
    r"(?:最多|至多|不能超过|不超过|上限(?:是|为)?)\s*(?:只|仅)?\s*(?:带|教|任课|上)?"
    r"\s*([0-9一二两三四五六七八九十]+)\s*个?\s*班")

# 教师不可用（整天或某时段）：动词关键词
T_OFF_KW = ("不上课", "不排课", "不上", "不排", "不想上", "不要上", "别上", "别排",
            "没空", "没时间", "请假", "有事", "不在校", "不在", "休息",
            "不出勤", "别安排", "不安排", "外出", "教研", "调休")
# 班级不可用：动词关键词
C_OFF_KW = ("不排课", "不上课", "不要排", "不排", "不安排课", "留空", "空着",
            "安排活动", "搞活动", "组织活动", "考试", "测验", "大扫除", "活动课")

# 追加（2026-10-03 用户实测）：上面 T_OFF_KW 里的词都是**连写**——
# 不上课 / 不想上 / 不排课。而教务很常写「不想周五上课」：否定词和「上课」
# 被星期词隔开，一个关键词都命中不了，整条要求就被判成"认不出"。
# 这里补这种间隔写法。
# ⚠️ 结尾不列「上」「排」单字——否则「不想周二上连堂」也会被算成"整天不可用"。
_T_OFF_GAP_RE = re.compile(
    r"(?:不想|不要|不能|不会|别|避免|避开|禁止)"
    r"\s*"
    r"(?:周[一二三四五六日天1-5]|星期[一二三四五六日天]|礼拜[一二三四五六日天])"
    r"[^，。；;、\s]{0,6}?"
    r"(?:上课|排课|有课|没课|值班|坐班)"
)

# 指定任课的连接符（与引擎一致：教 / =）
_ASSIGN_RE = re.compile(r"^(.+?)\s*[=教]\s*(.+)$")


# ---- 实体识别 ----------------------------------------------------------------

_ID_RE = re.compile(r"^([A-Za-z]+\s*\d{1,4})(?![0-9])")
# 教师姓名（可带编号后缀）：语文老师001 / 张老师 / 王马大吉老师
_TNAME_RE = re.compile(r"^([\u4e00-\u9fa5]{1,6}老师\d{0,4})")
# 裸姓名：2-3 个汉字，后面紧跟动词/时间词（避免把「数学老师」这类误当人名）
_BARE_RE = re.compile(
    r"^([\u4e00-\u9fa5]{2,3})(?=(?:不想|不要|不能|最多|至多|至少|别|没空|没时间"
    r"|请假|有事|不上|不排|不安排|不出|休息|带|教|周|星期|礼拜))")
_CID_RE = re.compile(r"(C\s*\d{1,4})")
# 班级名（如 高二(1)班 / 高一2班 / 3班）；「2个班」这类数量表达由调用方排除
_CNAME_RE = re.compile(
    r"([\u4e00-\u9fa5]{0,6}[（(]?\s*[0-9一二两三四五六七八九十]{1,3}\s*[)）]?\s*班)")


def _find_teacher(line, ctx):
    """从行首找教师。返回 (token, tid|None, err|None)。token 为空表示行首不是教师。"""
    m = _ID_RE.match(line)
    if m:
        token = m.group(1).replace(" ", "")
        tid, err = ctx.resolve_teacher(token)
        return token, tid, err
    m = _TNAME_RE.match(line)
    if m:
        token = m.group(1)
        tid, err = ctx.resolve_teacher(token)
        return token, tid, err
    m = _BARE_RE.match(line)
    if m:
        token = m.group(1)
        tid, err = ctx.resolve_teacher(token)
        return token, tid, err
    return "", None, None


def _find_class(text):
    """在 text 里找班级。返回 (token, 起始位置) 或 (None, -1)。

    「最多带2个班」里的「2个班」是数量不是班级，排除；
    「每班/各班/某班」是泛指，不是具体班级。
    """
    m = _CID_RE.search(text)
    if m:
        return m.group(1).replace(" ", ""), m.start()
    for m in _CNAME_RE.finditer(text):
        tok = m.group(1)
        if re.fullmatch(r"[0-9一二两三四五六七八九十]+\s*个?\s*班", tok):
            continue
        if re.search(r"(每|各|某|任何)班$", tok):
            continue
        return tok, m.start()
    return None, -1


# ---- 解析上下文（由 nl2req.py 注入 xlsx 数据；自测用合成数据子类化） ------------

class ParseContext:
    """词典与外部数据的解耦层：姓名解析、学科词表、连堂日口径。"""
    morning = 5    # 上午节数（与引擎 ScheduleRule.morning_periods 默认一致）
    per_day = 8    # 每天节数

    # ---- 以下方法由子类实现 ----
    def resolve_teacher(self, token):
        """教师 token（ID/姓名/张老师）→ (tid|None, 错误说明|None)。"""
        return None, "未接入教师解析"

    def resolve_class(self, token):
        """班级 token → (cid|None, 错误说明|None)。"""
        return None, "未接入班级解析"

    def match_subject(self, subj_raw, tid):
        """学科片段 → 标准学科名；认不出返回 None。"""
        return None

    def teacher_display(self, tid):
        return tid

    def teacher_info(self, tid):
        return None

    def double_day_of(self, subject):
        """该学科连堂固定在哪天（1-5）；非连堂学科返回 None。"""
        return None


# ---- 逐行解析 ----------------------------------------------------------------

def parse_line(raw: str, ctx: ParseContext):
    """解析一行自然语言。

    返回 dict：
      status: "ok" | "empty" | "unknown"
      constraints: [dict]（status=ok 时，一行可能拆出多条，如「周一和周三」）
      echo: 「我理解成……」人话回显（多条用；分隔）
      notes: [预警/说明]
      reason: 认不出的原因（status=unknown 时）
    """
    line = clean_line(raw)
    if not line:
        return {"status": "empty", "raw": raw, "constraints": [],
                "echo": "", "notes": [], "reason": ""}

    t_token, tid, t_err = _find_teacher(line, ctx)

    def fail(reason):
        return {"status": "unknown", "raw": raw, "constraints": [],
                "echo": "", "notes": [], "reason": reason}

    def ok(constraints):
        echoes = [describe_constraint(c, ctx) for c in constraints]
        notes = []
        for c in constraints:
            notes.extend(_precheck_notes(c, ctx))
        return {"status": "ok", "raw": line, "constraints": constraints,
                "echo": "；".join(echoes), "notes": notes, "reason": ""}

    # ① 教师带班数上限（先于指定任课：「不能教超过2个班」里也有「教」）
    m = _MAX_RE.search(line)
    if m:
        n = cn_num(m.group(1))
        if n is None or n < 1:
            return fail(f"带班数「{m.group(1)}」认不出，请写数字（如：最多带2个班）")
        if not tid:
            return fail(t_err or f"没认出这句是哪位老师（「{line}」），"
                                 f"请用教师姓名或ID开头")
        return ok([{"type": "teacher_max_classes", "teacher": tid, "max": n}])

    has_double = any(k in line for k in DOUBLE_KW)
    negated = any(k in line for k in NEG_KW)

    # ② 教师连堂禁日（例：张老师不想周二上连堂）
    if has_double and negated:
        if not tid:
            return fail(t_err or "这句话像是「某老师不想周X上连堂」，但没认出老师，"
                                 "请用教师姓名或ID开头")
        days = extract_days(line)
        if not days:
            return fail("认出老师和「连堂」，但没认出星期。请写成"
                        "「某老师不想周二上连堂」这样（周二/星期二/礼拜二都行）")
        return ok([{"type": "teacher_no_double_day", "teacher": tid, "days": days}])

    # ③ 班级不可用（例：C01班周五第7-8节不排课/安排活动；多天拆成多条）
    if any(k in line for k in C_OFF_KW):
        c_token, c_pos = _find_class(line)
        if c_token is not None:
            cid, err = ctx.resolve_class(c_token)
            if not cid:
                return fail(err or f"没认出班级「{c_token}」")
            days = extract_days(line)
            if not days:
                return fail("认出班级和「不排课」，但没认出星期。请写成"
                            "「某班周五第7-8节不排课」这样")
            periods = extract_periods(line, ctx.morning, ctx.per_day)
            # 「周一第1节和周三第7节」这种多天各带不同节次的写法无法用一条表达，
            # 拒绝并让用户拆行；「周一和周三下午」这种统一时段可以拆。
            if len(days) > 1 and len(_RANGE_RE.findall(line)) > 1:
                return fail("多个星期各带不同节次的写法目前不支持，"
                            "请拆成多行，每行一个星期")
            out = []
            for d in days:
                c = {"type": "class_unavailable", "class": cid, "day": d}
                if periods:
                    c["periods"] = list(periods)
                out.append(c)
            return ok(out)

    # ④ 教师不可用（例：张老师周二没空 / 张老师周二下午不排课 / 张老师不想周五上课）
    if tid and (any(k in line for k in T_OFF_KW) or _T_OFF_GAP_RE.search(line)):
        days = extract_days(line)
        if not days:
            return fail("认出老师，但没认出星期。请写成「某老师周二不上课」"
                        "或「某老师周二下午没空」这样")
        periods = extract_periods(line, ctx.morning, ctx.per_day)
        # 同班级分支：多天各带不同节次的写法拒绝，统一时段可拆
        if len(days) > 1 and len(_RANGE_RE.findall(line)) > 1:
            return fail("多个星期各带不同节次的写法目前不支持，"
                        "请拆成多行，每行一个星期")
        c = {"type": "teacher_unavailable", "teacher": tid, "days": days}
        if periods:
            c["periods"] = periods
        return ok([c])

    # ⑤ 指定任课（例：T30教C05数学 / 张老师教高二1班数学）
    m = _ASSIGN_RE.match(line)
    if m:
        left = m.group(1).strip()
        right = m.group(2).strip()
        tid2 = tid
        if not tid2 and left:
            tid2, err = ctx.resolve_teacher(left)
            if not tid2:
                return fail(err or f"「{left}」不在「教师」表里，请照抄表里的姓名或ID")
        if not tid2:
            return fail("这句话像是「某老师教某班某科」，但没认出老师")
        c_token, c_pos = _find_class(right)
        if c_token is None:
            return fail(f"「{right}」里没认出班级。写法：教师教班级学科"
                        f"（如 {t_token or 'T30'}教C05数学）")
        cid, err = ctx.resolve_class(c_token)
        if not cid:
            return fail(err or f"没认出班级「{c_token}」")
        subj_raw = right[c_pos + len(c_token):].strip("的 ")
        subj = ctx.match_subject(subj_raw, tid2)
        if not subj:
            return fail(f"「{subj_raw}」认不出学科，或不是该老师任教的学科。"
                        f"学科名要照「教师」sheet 的「任教学科」写")
        return ok([{"type": "assign_teacher", "teacher": tid2,
                    "class": cid, "subject": subj}])

    # ⑥ 认不出：弃权（绝不硬猜），由上层决定走 AI 提示词回退
    return fail("这句话认不出。目前支持：指定任课（张老师教C05数学）、"
                "老师某天/某节不上课、老师不想周X上连堂、某班周X第N节不排课、"
                "老师最多带N个班")


# ---- 回显与预警 ----------------------------------------------------------------

_DAY_NAMES = ["一", "二", "三", "四", "五"]


def _fmt_days(days):
    return "周" + "、周".join(_DAY_NAMES[d - 1] for d in days)


def _fmt_periods(periods, morning, per_day):
    if not periods:
        return ""
    if periods == list(range(1, morning + 1)):
        return "上午"
    if periods == list(range(morning + 1, per_day + 1)):
        return "下午"
    if len(periods) == 1:
        return f"第{periods[0]}节"
    if periods == list(range(periods[0], periods[-1] + 1)):
        return f"第{periods[0]}-{periods[-1]}节"
    return "第" + "、".join(str(p) for p in periods) + "节"


def describe_constraint(c, ctx: ParseContext = None) -> str:
    """「我理解成……」的人话回显（引擎侧 warning 复用同一口径）。"""
    t = c.get("type", "")
    teacher = c.get("teacher", "")
    tid_show = teacher
    if ctx is not None and teacher:
        tid_show = ctx.teacher_display(teacher)
    mp = ctx.morning if ctx else 5
    pd = ctx.per_day if ctx else 8
    if t == "assign_teacher":
        return f"指定 {tid_show} 教 {c.get('class')} 的「{c.get('subject')}」"
    if t == "teacher_unavailable":
        slot = _fmt_periods(c.get("periods", []), mp, pd)
        slot = (slot + " ") if slot else ""
        return f"{tid_show} {_fmt_days(c.get('days', []))} {slot}不排课"
    if t == "teacher_no_double_day":
        return f"{tid_show} {_fmt_days(c.get('days', []))} 不排连堂"
    if t == "class_unavailable":
        slot = _fmt_periods(c.get("periods", []), mp, pd)
        slot = (slot + " ") if slot else ""
        return (f"{c.get('class')} 周{_DAY_NAMES[c.get('day', 1) - 1]} {slot}"
                f"不排课（这些格将显示为自习）")
    if t == "teacher_max_classes":
        return f"{tid_show} 最多带 {c.get('max')} 个班"
    return str(c)


def _precheck_notes(c, ctx):
    """转换阶段的交叉预警（引擎阶段2还会再兜一道）。"""
    notes = []
    t = c.get("type", "")
    if t == "teacher_no_double_day":
        info = ctx.teacher_info(c.get("teacher", "")) or {}
        days = c.get("days", [])
        if info.get("subject"):
            dd = ctx.double_day_of(info["subject"])
            if dd is not None and dd in days:
                notes.append(
                    f"「{info['subject']}」的连堂固定在{_fmt_days([dd])}，"
                    f"这条要求生效后 {ctx.teacher_display(c['teacher'])} "
                    f"将无法担任任何「{info['subject']}」的任课教师"
                    f"（若某班只有他一位候选教师，会导致排课无解）")
    if t == "teacher_unavailable":
        days = c.get("days", [])
        if sorted(set(days)) == [1, 2, 3, 4, 5] and not c.get("periods"):
            notes.append(f"{ctx.teacher_display(c['teacher'])} 被设为全周不可用，"
                         f"他将排不到任何课（若他有课将导致无解）")
    if t == "teacher_max_classes":
        n = c.get("max", 0)
        if n and n < 2:
            notes.append(f"带班数上限为 {n}，这位教师几乎只能带 1 个班，"
                         f"请注意是否填反")
    return notes


# ---- AI 提示词回退（方案3：软件外转换，人工中转） --------------------------------

AI_PROMPT_TEMPLATE = """你是一名排课约束解析助手。下面是教务写的几条要求（真实姓名已替换为编号）。
请把每一条转换成结构化约束，**只输出一个 JSON 对象，不要输出任何解释**。

只允许输出以下格式（version 必须为 1，days 用 1-5 表示周一到周五，
periods 用节次数字，上午为 1-5、下午为 6-8）：

{"version": 1, "constraints": [
  {"type": "assign_teacher",       "teacher": "T30", "class": "C05", "subject": "数学"},
  {"type": "teacher_unavailable",  "teacher": "T30", "days": [2], "periods": [6, 7, 8]},
  {"type": "teacher_no_double_day","teacher": "T30", "days": [2]},
  {"type": "class_unavailable",    "class": "C01", "day": 5, "periods": [7, 8]},
  {"type": "teacher_max_classes",  "teacher": "T30", "max": 2}
]}

严格规则：
- 只能输出上面五种 type，字段名照抄；teacher_unavailable 的 periods 省略表示全天；
  class_unavailable 的 day 是单数（一天一条）。
- 认不出或没把握的一行，**不要编造**：把原句放进 "unrecognized" 数组。
- 只输出一个 JSON 对象。

待解析的要求：
{lines}
"""


def build_ai_prompt(lines):
    """未命中行的提示词（行内姓名必须已由调用方替换为编号后再传入）。"""
    numbered = "\n".join(f"{i}. {ln}" for i, ln in enumerate(lines, 1))
    # ⚠️ 不能用 str.format：模板正文里含 JSON 示例（{"version": 1, ...}），
    #    花括号会被当成格式字段 → KeyError: '"version"'（2026-10-03 实测）。
    #    一旦触发，--line 与 --text 两个模式都会崩，等于「认不出的行走 AI 兜底」
    #    这条路完全不可用。改用 replace 占位符：不必要求模板作者记得转义花括号。
    return AI_PROMPT_TEMPLATE.replace("{lines}", numbered)
