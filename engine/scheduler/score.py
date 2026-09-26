# -*- coding: utf-8 -*-
"""方案评分：把求解结果的原始指标换算成 0–100 的**绝对质量分**。

与求解解耦：无论各套方案用什么权重档位求得，都用**同一把尺子**评分，
分数才有可比性。

背景：此前用的是「相对排名分」——按各方案在同一批里的排名映射成 100–70 分。
一旦几套方案的指标相同（此前实测 5 套指标完全一致），就会全部并列第 1、
全部 100 分，用户看到的是 5 个一模一样的「满分方案」。绝对分不受此影响。
"""
from typing import Dict

from .config import NUM_DAYS

# 各维度权重（合计 1.00）
WEIGHTS: Dict[str, float] = {
    "首节主科": 0.20,
    "教师均衡": 0.20,
    "主科分散": 0.15,
    "自习后置": 0.15,
    "主科在上午": 0.15,
    "体育首末": 0.15,
}


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def sub_scores(m: Dict[str, float], cfg, n_classes: int) -> Dict[str, float]:
    """把原始指标换算成各维度 0–100 子分。

    不适用的维度（如课表里没有自习、没有体育）返回 None，
    总分计算时按权重比例重分配，避免拉低或虚高。
    """
    n_cd = max(1, n_classes * NUM_DAYS)          # 班级数 × 天数
    n_per = max(1, cfg.schedule.periods_per_day)

    s: Dict[str, float] = {}

    # 首节主科率：越高越好
    s["首节主科"] = _clamp(m.get("first_core_rate", 0.0))

    # 自习后置率：越高越好；课表里没有自习则不适用
    s["自习后置"] = (_clamp(m.get("selfstudy_pm_rate", 0.0))
                    if m.get("ss_total", 0) > 0 else None)

    # 下午主科率：越低越好，故取反
    s["主科在上午"] = _clamp(100.0 - m.get("pm_core_rate", 0.0))

    # 主科单日超额节数：越少越好（上界保守取 班·天 × 2）
    s["主科分散"] = _clamp(
        100.0 * (1 - min(1.0, m.get("core_over", 0) / (n_cd * 2))))

    # 体育排在首/末节的次数：越少越好；没有体育课则不适用
    s["体育首末"] = (_clamp(
        100.0 * (1 - min(1.0, m.get("pe_violation", 0) / (n_cd * 2))))
        if m.get("pe_total", 0) > 0 else None)

    def span_score(v: float) -> float:
        # 教师日课时极差：1 节 → 100 分；n_per 节 → 0 分
        return _clamp(100.0 * (1 - min(1.0, (v - 1.0) / max(1, n_per - 1))))

    s["教师均衡"] = (0.6 * span_score(m.get("teacher_avg_span", 1.0))
                    + 0.4 * span_score(m.get("teacher_max_span", 1.0)))
    return s


def total_score(sub: Dict[str, float]) -> float:
    """按 WEIGHTS 加权求和；跳过不适用维度并按比例重分配，保证恒在 0–100。"""
    num = den = 0.0
    for key, w in WEIGHTS.items():
        v = sub.get(key)
        if v is None:
            continue
        num += w * v
        den += w
    if den <= 0:
        return 0.0
    return round(num / den, 1)


def absolute_score(m: Dict[str, float], cfg, n_classes: int):
    """一步到位：返回 (总分, 各维度子分)。"""
    sub = sub_scores(m, cfg, n_classes)
    return total_score(sub), sub
