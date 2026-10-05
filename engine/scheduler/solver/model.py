# -*- coding: utf-8 -*-
"""CP-SAT 建模层：硬约束 + 软约束惩罚项。

硬约束（对应 v1 基线）：
  H1 每格唯一   每班每天每节恰好排一门（自习作为普通格参与）
  H2 周课时     每班每科节数 == input.xlsx 需求
  H3 教师冲突   同一教师同一天同一节最多带 1 个班
  H4 固定课     指定 (天,节) 强制为指定学科（班会/研究性学习/校本课）
  H5 连堂       连堂学科在其连堂日恰好排 N 个连续块，且该天该科节次必须落在块内
  H6 分科       由 input.xlsx 的班级「选科」+ 课时标准决定各班的学科集合（数据层生效）
  H7 作息       节次→时钟由 config.compute_period_labels 计算（已修正 R2 午休缺失）

软约束（加权惩罚进目标函数，权重可在 hard_limits.json 的 soft_weights 配置）：
  S1 主科分散      语数英等主科每天不超过 2 节
  S2 体育不排首末  体育课不排第 1 节与当天最后一节
  S3 教师日均均衡  教师每日课时尽量接近其日均
  S4 首节主科      第 1 节优先排主科（奖励，负惩罚）
  S5 自习后置      自习尽量排在下午
"""
import math
from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Any

from ortools.sat.python import cp_model

from ..config import Config, NUM_DAYS, SELF_STUDY, ARTS_SUBJECTS
from ..data.load import Problem, NO_TEACHER_SUBJECTS

# 变量键：(班级ID, 学科, 天(1-based), 节次(1-based))
Key = Tuple[str, str, int, int]

PE_SUBJECT = "体育"
CORE_PER_DAY_LIMIT = 2  # S1：主科每天最多节数


@dataclass
class SoftGroup:
    """一组软约束惩罚项。

    权重**不在建模时固化**，而是留到目标层再乘，这样同一份模型可以为
    每套方案换一套权重（方案差异化的关键）。
    """
    key: str
    exprs: List[Any] = field(default_factory=list)
    sign: int = 1  # +1 惩罚 / -1 奖励


@dataclass
class ModelBundle:
    model: cp_model.CpModel
    x: Dict[Key, Any]
    terms: List[Tuple[int, Any]] = field(default_factory=list)
    soft_terms: Dict[str, SoftGroup] = field(default_factory=dict)
    problem: Problem = None
    cfg: Config = None
    subjects_by_class: Dict[str, List[str]] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    # ---- P0：教师成为决策变量 ----
    y: Dict[Tuple[str, str, str], Any] = field(default_factory=dict)   # (班级,学科,教师)
    z: Dict[Tuple[str, str, str, int, int], Any] = field(default_factory=dict)  # z = x AND y
    cand_of: Dict[Tuple[str, str], List[str]] = field(default_factory=dict)     # (班级,学科)->候选
    cand_pairs_of_t: Dict[str, List[Tuple[str, str]]] = field(default_factory=dict)  # 教师->候选课程
    zs_by_td: Dict[Tuple[str, int], List[Any]] = field(default_factory=dict)    # (教师,天)->z
    week_total_of_t: Dict[str, Any] = field(default_factory=dict)      # 教师周课时 IntVar

    def objective_expr(self, weights: Dict[str, int]):
        """按给定权重档位组装目标表达式；全部为 0 时返回 None。"""
        expr = None
        for key, g in self.soft_terms.items():
            w = int(weights.get(key, 0) or 0)
            if w and g.exprs:
                term = w * g.sign * sum(g.exprs)
                expr = term if expr is None else expr + term
        return expr


def apply_objective(bundle: ModelBundle, weights: Dict[str, int]) -> None:
    """按给定权重档位重设目标函数（覆盖式）。

    CpModel.Minimize() 内部会先清空旧目标，因此可以在同一个模型上反复调用，
    为每套方案换一个优化目标，无需重建模型（25 班建模成本只付一次）。
    """
    try:
        bundle.model.clear_objective()
    except AttributeError:
        pass  # 老版本 ortools 没有该 API，Minimize 本身也会覆盖
    expr = bundle.objective_expr(weights)
    bundle.model.Minimize(expr if expr is not None else 0)


def build_model(p: Problem) -> ModelBundle:
    cfg = p.config
    model = cp_model.CpModel()
    periods = cfg.periods()                       # [1..8]
    days = list(range(1, NUM_DAYS + 1))           # [1..5]
    n_per = cfg.schedule.periods_per_day

    # ---------- 学科 / 课时 / 教师 索引 ----------
    subjects_by_class: Dict[str, List[str]] = {}
    req: Dict[Tuple[str, str], int] = {}
    teacher_of: Dict[Tuple[str, str], Any] = {}
    block_len_of: Dict[Tuple[str, str], int] = {}
    for c in p.courses:
        subjects_by_class.setdefault(c.class_id, []).append(c.subject)
        req[(c.class_id, c.subject)] = c.weekly
        teacher_of[(c.class_id, c.subject)] = c.teacher_id
        block_len_of[(c.class_id, c.subject)] = c.block_len

    # ---------- 决策变量 x[(班级, 学科, 天, 节)] ----------
    x: Dict[Key, Any] = {}
    for cid, subs in subjects_by_class.items():
        for s in subs:
            for d in days:
                for per in periods:
                    x[(cid, s, d, per)] = model.NewBoolVar(f"x_{cid}_{s}_{d}_{per}")

    warnings: List[str] = []

    # ---------- H1 每格唯一 ----------
    for cid, subs in subjects_by_class.items():
        for d in days:
            for per in periods:
                model.AddExactlyOne(x[(cid, s, d, per)] for s in subs)

    # ---------- H2 周课时 ----------
    for (cid, s), n in req.items():
        model.Add(sum(x[(cid, s, d, per)] for d in days for per in periods) == n)

    # ---------- 教师候选索引（P0）----------
    # 只为「需要教师」且「有候选」的课程建索引；NO_TEACHER_SUBJECTS 不参与。
    cand_of: Dict[Tuple[str, str], List[str]] = {}
    cand_pairs_of_t: Dict[str, List[Tuple[str, str]]] = {}
    for c in p.courses:
        if c.subject in NO_TEACHER_SUBJECTS:
            continue
        cands = [t for t in (c.teacher_candidates or []) if t in p.teachers]
        if not cands:
            warnings.append(
                f"班级 {c.class_id} 的「{c.subject}」没有可用任课教师，该课程不参与教师冲突约束")
            continue
        cand_of[(c.class_id, c.subject)] = cands
        for t in cands:
            cand_pairs_of_t.setdefault(t, []).append((c.class_id, c.subject))

    # ---------- H25 教师任职资格 + H3' 教师冲突（P0）----------
    # y[(班级,学科,教师)] = 1 表示该门课由该教师任教。
    # (a) 只能教自己学科：由 y 的定义域保证 —— 候选本身就只来自同学科教师。
    # (b) 每门课恰好一位教师：AddExactlyOne。
    y: Dict[Tuple[str, str, str], Any] = {}
    for (cid, s), cands in cand_of.items():
        vs = []
        for t in cands:
            v = model.NewBoolVar(f"y_{cid}_{s}_{t}")
            y[(cid, s, t)] = v
            vs.append(v)
        model.AddExactlyOne(vs)

    # z = x AND y 的线性化：z<=x, z<=y, z>=x+y-1
    # ★ 第三条绝对不可省：少了它 z 可以恒取 0，下面的 AddAtMostOne 会空转，
    #   教师冲突被绕过且不报错。
    z: Dict[Tuple[str, str, str, int, int], Any] = {}
    zs_by_slot: Dict[Tuple[str, int, int], List[Any]] = {}
    zs_by_td: Dict[Tuple[str, int], List[Any]] = {}
    for (cid, s), cands in cand_of.items():
        for t in cands:
            yv = y[(cid, s, t)]
            for d in days:
                for per in periods:
                    xv = x[(cid, s, d, per)]
                    zv = model.NewBoolVar(f"z_{cid}_{s}_{t}_{d}_{per}")
                    model.Add(zv <= xv)
                    model.Add(zv <= yv)
                    model.Add(zv >= xv + yv - 1)
                    z[(cid, s, t, d, per)] = zv
                    zs_by_slot.setdefault((t, d, per), []).append(zv)
                    zs_by_td.setdefault((t, d), []).append(zv)

    for (_t, _d, _per), lst in zs_by_slot.items():
        model.AddAtMostOne(lst)      # 同一教师同一天同一节最多带 1 个班

    # ---------- H26 教师带班数上下界（对称性破除 + 减少闲置）----------
    # 13 位语文教师对 25 个班完全对称，求解器会在等价解上白耗；
    # 限制带班数可剪掉"1 人带 6 班、6 人闲置"的整个对称子空间。
    if getattr(cfg.solver, "balance_class_count", True) and cand_of:
        count_by_subject: Dict[str, int] = {}
        pool_by_subject: Dict[str, set] = {}
        for (cid, s), cands in cand_of.items():
            count_by_subject[s] = count_by_subject.get(s, 0) + 1
            pool_by_subject.setdefault(s, set()).update(cands)
        locked_keys = {(c.class_id, c.subject) for c in p.courses if c.locked}

        # 结构化"教师不可用"涉及的教师：下界放宽为 0。连堂学科被 H5c 强制
        # "非连堂日每天恰好 1 节"时，整日禁排某天 = 该科不可教，lo=1 会制造
        # 假性无解（2026-10-03 小模型实测）。下界只是启发式，放宽不影响正确性。
        unavail_teachers = {sc.teacher
                            for sc in (getattr(p, "structured", None) or [])
                            if sc.type == "teacher_unavailable"}

        for t, pairs in cand_pairs_of_t.items():
            ti = p.teachers.get(t)
            s = ti.subject if ti else None
            if not s or s not in count_by_subject:
                continue
            n_course = count_by_subject[s]
            n_teacher = len(pool_by_subject.get(s, ()))
            lo = 1 if (n_course >= n_teacher and t not in unavail_teachers) else 0
            hi = math.ceil(n_course / max(1, n_teacher)) + 1
            # 显式指派可突破上界，否则会与用户意图冲突导致无解
            locked_n = sum(1 for pair in pairs if pair in locked_keys)
            hi = max(hi, locked_n)
            upper = max(lo, min(hi, len(pairs)))
            n_t = model.NewIntVar(lo, upper, f"n_{t}")
            model.Add(n_t == sum(y[(cid, ss, t)] for (cid, ss) in pairs))

    # ---------- H29 班主任须在本班至少任课 1 节（设置页开关）----------
    # 只有当"教师分配纳入求解"（存在 y）时才表达得了；候选的补充在
    # load.apply_homeroom_own_class 里做（那把班主任补进本班课程候选）。
    if getattr(cfg, "homeroom_must_teach_own", False):
        if not cand_of:
            warnings.append(
                "已勾选「班主任须在自己班上任课」，但本次排课未启用教师分配决策，本条未生效")
        else:
            covered, uncovered = 0, 0
            for ci in p.classes:
                tid = (getattr(ci, "homeroom", "") or "").strip()
                if not tid:
                    continue
                vs = [y[(ci.id, s, tid)] for s in subjects_by_class.get(ci.id, [])
                      if (ci.id, s, tid) in y]
                if not vs:
                    uncovered += 1        # 原因已在加载层逐类告警，这里只报个数
                    continue
                model.Add(sum(vs) >= 1)
                covered += 1
            if covered:
                warnings.append(
                    f"班主任必须教自己带的班：已生效 —— {covered} 个班的班主任都排到了"
                    f"本班的正式课（班会的固定课不计）")
            if uncovered:
                warnings.append(
                    f"班主任必须教自己带的班：另有 {uncovered} 个班没能覆盖"
                    f"（原因见上面的告警）")

    # 回退路径：无候选（如全部显式指派、或未启用教师决策）时保留常量分组
    by_teacher: Dict[str, List[Tuple[str, str]]] = {}
    if not cand_of:
        for (cid, s), t in teacher_of.items():
            if t:
                by_teacher.setdefault(t, []).append((cid, s))
        for t, pairs in by_teacher.items():
            for d in days:
                for per in periods:
                    model.AddAtMostOne(x[(cid, s, d, per)] for (cid, s) in pairs)

    # ---------- H4 固定课 ----------
    for fc in cfg.fixed_classes:
        if not (1 <= fc.day <= NUM_DAYS) or fc.period not in periods:
            warnings.append(f"固定课「{fc.subject}」的天/节次({fc.day},{fc.period})超出范围，已忽略")
            continue
        hit = 0
        for cid, subs in subjects_by_class.items():
            if fc.subject in subs:
                model.Add(x[(cid, fc.subject, fc.day, fc.period)] == 1)
                hit += 1
        # hit == 0 时不重复告警：数据层 validate.py 已统一提示「固定课未出现在课程表」

    # ---------- H5 连堂 ----------
    consec = cfg.consecutive
    # 连堂起始格变量按 (班级, 学科, 天) 归档，供「教师连堂禁日」等结构化约束引用
    b_starts: Dict[Tuple[str, str, int], Dict[int, Any]] = {}
    for (cid, s), blen in block_len_of.items():
        if blen <= 1:
            continue
        day = consec.day_of(s)
        if day is None:
            warnings.append(
                f"班级 {cid} 的「{s}」设置了连堂，但该学科不在连堂规则内，已按普通课时排"
            )
            continue
        if not (1 <= day <= NUM_DAYS):
            continue
        max_start = n_per - blen + 1
        candidates = [p0 for p0 in range(1, max_start + 1)]
        allowed = [p0 for p0 in candidates if p0 in consec.allow_start_periods] or candidates

        B: Dict[int, Any] = {}
        for p0 in allowed:
            v = model.NewBoolVar(f"b_{cid}_{s}_{day}_{p0}")
            B[p0] = v
            for k in range(blen):
                if p0 + k in periods:
                    model.AddImplication(v, x[(cid, s, day, p0 + k)])
        b_starts[(cid, s, day)] = B
        model.Add(sum(B.values()) == max(1, consec.blocks_per_day))

        # 该天该科的每一节都必须落在某个块内（杜绝零星单节）
        for per in periods:
            covering = [B[p0] for p0 in allowed if p0 <= per <= p0 + blen - 1]
            if covering:
                model.Add(x[(cid, s, day, per)] <= sum(covering))
            else:
                model.Add(x[(cid, s, day, per)] == 0)

    # ---------- H5b 禁止相邻排课（杜绝"伪连堂"） ----------
    # 规格：一门课一周只允许一次连堂，且必须落在它的指定连堂日（由 H5 保证）。
    # 因此相邻排课的禁则要覆盖所有学科：
    #   · 非连堂学科（blen<=1、自习除外）：任何一天都不得相邻排课
    #   · 连堂学科（blen>1）：指定日交给 H5（恰好一个连堂块），
    #     其余各天一律不得相邻排课 —— 否则语数英会在别的天再长出一个连堂，
    #     违反"一周只有一次连堂"。
    for (cid, s), blen in block_len_of.items():
        if s == SELF_STUDY:
            continue  # 自习允许连续
        desig = consec.day_of(s) if blen > 1 else None
        for d in days:
            if desig is not None and d == desig:
                continue  # 指定日由 H5 处理
            for per in range(1, n_per):  # per 和 per+1 相邻
                model.Add(x[(cid, s, d, per)] + x[(cid, s, d, per + 1)] <= 1)

    # ---------- H5c 连堂学科：非指定日每天至多 cap 节 ----------
    # 规格：周课时扣掉连堂块之后，余下的节数应在其余各天均匀铺开，
    # 使课表呈"每天一节 + 指定日连堂"，而不是挤在少数几天。
    #
    # cap 按周课时推算，保证永远可行：
    #   cap = ceil((周课时 - 连堂块节数) / 非指定日天数)
    #   周课时 6、连堂 2、其余 4 天  -> cap = 1（每天恰好一节）
    #   周课时 7、连堂 2、其余 4 天  -> cap = 2（否则无解，故自动放宽并告警）
    for (cid, s), blen in block_len_of.items():
        if blen <= 1:
            continue
        desig = consec.day_of(s)
        if desig is None:
            continue  # 非连堂规则内的学科，H5 已告警并按普通课时处理
        other_days = [d for d in days if d != desig]
        if not other_days:
            continue
        rest = req[(cid, s)] - blen
        cap = max(1, math.ceil(rest / len(other_days)))
        if cap > 1:
            warnings.append(
                f"班级 {cid} 的「{s}」周课时 {req[(cid, s)]} 节，"
                f"扣除连堂 {blen} 节后仍有 {rest} 节，"
                f"无法做到其余每天至多 1 节（已放宽为每天至多 {cap} 节）"
            )
        for d in other_days:
            model.Add(sum(x[(cid, s, d, per)] for per in periods) <= cap)

    # ---------- 结构化额外约束（约束转换器 → schema v1，见 docs/约束能力清单.md） ----------
    # 逐条落成 CP-SAT 约束并回显「已生效」——绝不静默（评估文档 §7 配套要求）。
    # 可行性预检（全员禁连堂、锁定超上限等）在阶段2 validate.py 已秒级拒绝。
    for sc in getattr(p, "structured", None) or []:
        pairs = cand_pairs_of_t.get(sc.teacher, []) if sc.teacher else []
        if sc.type in ("teacher_unavailable", "teacher_no_double_day",
                       "teacher_max_classes") and not pairs:
            warnings.append(
                f"结构化约束未生效：{sc.describe(p)}（该教师没有进入任何课程的候选集）")
            continue

        if sc.type == "teacher_unavailable":
            # H4（教师不可排时段）：该教师的每门候选课在禁排格不能由他上
            ds = sc.days or list(days)
            ps = sc.periods or list(periods)
            for (cid, s) in pairs:
                for d in ds:
                    for per in ps:
                        model.Add(x[(cid, s, d, per)] + y[(cid, s, sc.teacher)] <= 1)
            warnings.append(f"结构化约束已生效：{sc.describe(p)}")

        elif sc.type == "teacher_no_double_day":
            # 教师的候选课里，连堂日落在禁日的：连堂起始格与任课互斥
            # ——语义后果是该教师不担任该学科任课（连堂日按学科固定）。
            hit = 0
            for (cid, s) in pairs:
                if block_len_of.get((cid, s), 0) <= 1:
                    continue
                dday = consec.day_of(s)
                if dday is None or dday not in sc.days:
                    continue
                B = b_starts.get((cid, s, dday))
                if not B:
                    continue
                for bv in B.values():
                    model.Add(bv + y[(cid, s, sc.teacher)] <= 1)
                hit += 1
            if hit:
                warnings.append(f"结构化约束已生效：{sc.describe(p)}"
                                f"（涉及 {hit} 门候选课）")
            else:
                warnings.append(
                    f"结构化约束未在模型层产生额外限制：{sc.describe(p)}"
                    f"（候选已收缩、该教师无相关课程，或连堂不落在禁日）")

        elif sc.type == "class_unavailable":
            # 班级禁排格：所有学科置 0，格子由自习填充（H1 仍保证每格恰好一门）
            subs = [s for s in subjects_by_class.get(sc.class_id, [])
                    if s != SELF_STUDY]
            if not subs:
                warnings.append(
                    f"结构化约束未生效：{sc.describe(p)}（该班级没有普通课程）")
                continue
            ds = sc.days or []
            ps = sc.periods or list(periods)
            for d in ds:
                for per in ps:
                    for s in subs:
                        model.Add(x[(sc.class_id, s, d, per)] == 0)
            warnings.append(f"结构化约束已生效：{sc.describe(p)}")

        elif sc.type == "teacher_max_classes":
            # 叠加在 H26 的自动推导上下界之上（n_t == sum(y)，此处再收紧）
            model.Add(sum(y[(cid, s, sc.teacher)] for (cid, s) in pairs)
                      <= sc.max_classes)
            warnings.append(f"结构化约束已生效：{sc.describe(p)}")

    # ---------- 软约束 ----------
    # 辅助变量**无条件创建**（不再按权重 > 0 门控），权重只在目标层生效，
    # 这样换权重档位时不会因为变量缺失而无法重设目标。
    # 默认权重全 > 0，故默认档位下模型规模与此前完全一致。
    soft_terms: Dict[str, SoftGroup] = {
        "spread_core": SoftGroup("spread_core"),
        "pe_not_first_last": SoftGroup("pe_not_first_last"),
        "teacher_balance": SoftGroup("teacher_balance"),
        "core_morning_first": SoftGroup("core_morning_first", sign=-1),
        "selfstudy_afternoon": SoftGroup("selfstudy_afternoon"),
        # 新增：文档 S5 / S16 / S12，以及 S18 教师周课时极差均衡
        "core_morning": SoftGroup("core_morning"),
        "arts_afternoon": SoftGroup("arts_afternoon"),
        "no_stack": SoftGroup("no_stack"),
        "teacher_week_balance": SoftGroup("teacher_week_balance"),
    }
    w = cfg.soft
    core_subjects = set(consec.subjects)

    # S1 主科分散：主科每天不超过 CORE_PER_DAY_LIMIT 节
    for cid, subs in subjects_by_class.items():
        for s in subs:
            if s not in core_subjects:
                continue
            for d in days:
                cnt = model.NewIntVar(0, n_per, f"cnt_{cid}_{s}_{d}")
                model.Add(cnt == sum(x[(cid, s, d, per)] for per in periods))
                over = model.NewIntVar(0, n_per, f"ovr_{cid}_{s}_{d}")
                model.Add(over >= cnt - CORE_PER_DAY_LIMIT)
                soft_terms["spread_core"].exprs.append(over)

    # S2 体育不排第 1 节 / 最后一节
    last = n_per
    for cid, subs in subjects_by_class.items():
        if PE_SUBJECT not in subs:
            continue
        for d in days:
            soft_terms["pe_not_first_last"].exprs.append(x[(cid, PE_SUBJECT, d, 1)])
            soft_terms["pe_not_first_last"].exprs.append(x[(cid, PE_SUBJECT, d, last)])

    # S3 教师日均均衡：超出日均上限的部分计罚
    # 改造：total/cap 由预分配常量改为依赖 y 的变量，daily 改用 z（x AND y）。
    # 否则预分配不均衡时 cap 会把不均衡"合法化"（实测 36 人被压到 12 节/周却罚不下去）。
    grid = cfg.grid_size()
    week_total_of_t: Dict[str, Any] = {}
    for t, pairs in cand_pairs_of_t.items():
        # 周课时 = Σ y[课,教师] × 该课周课时（常量 × BoolVar，合法线性项）
        total = model.NewIntVar(0, grid, f"tt_{t}")
        model.Add(total == sum(req[(cid, s)] * y[(cid, s, t)] for (cid, s) in pairs))
        week_total_of_t[t] = total

        # cap = ceil(total / NUM_DAYS)：用两条线性约束实现（AddDivisionEquality 是 floor）
        # 下界必须是 0：教师可能一位课都没分到（total=0），此时 ceil(0/5)=0，
        # 若把下界设成 1 会与 cap*5 <= total+4 矛盾，直接让整个模型无解。
        cap = model.NewIntVar(0, grid, f"cap_{t}")
        model.Add(cap * NUM_DAYS >= total)
        model.Add(cap * NUM_DAYS <= total + NUM_DAYS - 1)

        for d in days:
            daily = model.NewIntVar(0, n_per, f"tl_{t}_{d}")
            model.Add(daily == sum(zs_by_td.get((t, d), [])))
            over = model.NewIntVar(0, n_per, f"tov_{t}_{d}")
            model.Add(over >= daily - cap)
            soft_terms["teacher_balance"].exprs.append(over)

    # S4 首节主科（奖励 → 负惩罚）
    for cid, subs in subjects_by_class.items():
        for s in subs:
            if s not in core_subjects:
                continue
            for d in days:
                soft_terms["core_morning_first"].exprs.append(x[(cid, s, d, 1)])

    # S5 自习后置：上午（<= 上午节数）的自习计罚
    mp = cfg.schedule.morning_periods
    for cid, subs in subjects_by_class.items():
        if SELF_STUDY not in subs:
            continue
        for d in days:
            for per in periods:
                if per <= mp:
                    soft_terms["selfstudy_afternoon"].exprs.append(
                        x[(cid, SELF_STUDY, d, per)])

    # 文档 S5 主科优先上午：惩罚排在下午的主科（与只奖励第 1 节的 S4 互补）
    for cid, subs in subjects_by_class.items():
        for s in subs:
            if s not in core_subjects:
                continue
            for d in days:
                for per in periods:
                    if per > mp:
                        soft_terms["core_morning"].exprs.append(x[(cid, s, d, per)])

    # 文档 S16 术科排下午：惩罚排在上午的术科
    for cid, subs in subjects_by_class.items():
        for s in subs:
            if s not in ARTS_SUBJECTS:
                continue
            for d in days:
                for per in periods:
                    if per <= mp:
                        soft_terms["arts_afternoon"].exprs.append(x[(cid, s, d, per)])

    # 文档 S12 同科不堆叠：同班同科同天超过 2 节的部分计罚
    # 跳过主科（已由 S1 spread_core 覆盖）与连堂指定日（与 H5 自相矛盾）
    for (cid, s), _blen in block_len_of.items():
        if s in core_subjects:
            continue
        desig = consec.day_of(s) if _blen > 1 else None
        for d in days:
            if desig is not None and d == desig:
                continue
            cnt = model.NewIntVar(0, n_per, f"sk_{cid}_{s}_{d}")
            model.Add(cnt == sum(x[(cid, s, d, per)] for per in periods))
            over = model.NewIntVar(0, n_per, f"sko_{cid}_{s}_{d}")
            model.Add(over >= cnt - CORE_PER_DAY_LIMIT)
            soft_terms["no_stack"].exprs.append(over)

    # 新增 S18 教师周课时极差均衡：惩罚 (最大周课时 - 最小周课时)。
    # 这才是真正的"课量均衡" —— S3 只管日均且 cap 会自适应，管不住周总量。
    if week_total_of_t:
        wmax = model.NewIntVar(0, grid, "wmax")
        wmin = model.NewIntVar(0, grid, "wmin")
        for _t, tot in week_total_of_t.items():
            model.Add(wmax >= tot)
            model.Add(wmin <= tot)
        span = model.NewIntVar(0, grid, "wspan")
        model.Add(span >= wmax - wmin)
        soft_terms["teacher_week_balance"].exprs.append(span)

    # 热启动：把贪心得到的预期分配作为 hint，保证加 y 后的解不比改造前差。
    # hint 是模型属性，对后续各权重档位的 Solve 都生效。
    for (cid, s), cands in cand_of.items():
        h = p.hint_assign.get((cid, s))
        if h in cands:
            model.AddHint(y[(cid, s, h)], 1)
        elif cands:
            model.AddHint(y[(cid, s, cands[0])], 1)

    bundle = ModelBundle(
        model=model,
        x=x,
        soft_terms=soft_terms,
        problem=p,
        cfg=cfg,
        subjects_by_class=subjects_by_class,
        warnings=warnings,
        y=y,
        z=z,
        cand_of=cand_of,
        cand_pairs_of_t=cand_pairs_of_t,
        zs_by_td=zs_by_td,
        week_total_of_t=week_total_of_t,
    )
    # 默认档位：用配置里的权重，行为与改造前完全一致
    apply_objective(bundle, {
        "spread_core": w.spread_core,
        "pe_not_first_last": w.pe_not_first_last,
        "teacher_balance": w.teacher_balance,
        "core_morning_first": w.core_morning_first,
        "selfstudy_afternoon": w.selfstudy_afternoon,
        "core_morning": w.core_morning,
        "arts_afternoon": w.arts_afternoon,
        "no_stack": w.no_stack,
        "teacher_week_balance": w.teacher_week_balance,
    })
    return bundle
