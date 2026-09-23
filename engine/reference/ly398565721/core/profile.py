from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Literal, Mapping


RuleKind = Literal["hard", "soft", "conditional", "process"]


@dataclass(frozen=True)
class RuleSetting:
    id: str
    title: str
    kind: RuleKind
    value: Any
    confirmation_required: bool = False


@dataclass(frozen=True)
class SchoolProfile:
    rules: Mapping[str, RuleSetting]

    def with_confirmation(self, rule_id: str, value: Any) -> "SchoolProfile":
        if rule_id not in self.rules:
            raise KeyError(f"未知确认项：{rule_id}")
        setting = self.rules[rule_id]
        if not setting.confirmation_required:
            raise ValueError(f"{rule_id} 不是每次必确认项")
        updated = dict(self.rules)
        updated[rule_id] = replace(setting, value=value)
        return SchoolProfile(updated)

    def unanswered_confirmations(self) -> tuple[RuleSetting, ...]:
        return tuple(
            setting
            for setting in self.rules.values()
            if setting.confirmation_required and setting.value is None
        )


def _rule(rule_id: str, title: str, kind: RuleKind, value: Any = True, *, required: bool = False) -> RuleSetting:
    return RuleSetting(rule_id, title, kind, value, required)


def default_profile() -> SchoolProfile:
    """Return the confirmed merged catalog with unanswered per-run choices."""
    rules = [
        _rule("F01", "班级同一时段最多一门正式课", "hard"),
        _rule("F02", "教师同一时段最多教一个班", "hard"),
        _rule("F03", "周课时精确满足聘任表", "hard"),
        _rule("F04", "固定上课日、节次和占用时段", "conditional", None, required=True),
        _rule("F05", "学科、教师和角色禁排", "conditional", None, required=True),
        _rule("F06", "体信音美不得排第1、2节", "hard"),
        _rule("F07", "教师同日半天上课", "hard"),
        _rule("F08", "单班教师每天最多2节正式课", "hard"),
        _rule("F09", "两班教师每天最多3节且不得同班3节", "hard"),
        _rule("F10", "多班教师优先每天不超过3节，周课时超过12节豁免", "soft"),
        _rule("F11", "周课时少于5每天最多1节", "hard"),
        _rule("F12", "周课时5每天1节", "hard"),
        _rule("F13", "周课时6每天至少1节并有1天连堂", "hard"),
        _rule("F14", "周课时7每天至少1节并有2天连堂", "hard"),
        _rule("F15", "连堂不得跨第4、5节午休", "hard"),
        _rule("F16", "同班每天体育最多1节，周课时按聘任表", "hard"),
        _rule("F17", "共享教师与资源不撞课", "conditional", None, required=True),
        _rule("F18", "特殊班型课时与固定时段", "conditional", None, required=True),
        _rule("F19", "具名教师特殊安排", "conditional", None, required=True),
        _rule("F20", "数学教师每日最多3节数学且不得双班双节", "hard"),
        _rule("S01", "班主任任教学科优先周一上午", "soft"),
        _rule("S02", "课量与上下午均衡", "soft"),
        _rule("S03", "语数英周内均匀", "soft"),
        _rule("S04", "减少空档和等待", "soft"),
        _rule("S05", "避免同班同科一天超过2节或连上3节", "soft"),
        _rule("D01", "值班日期、时段与每班覆盖", "conditional", None, required=True),
        _rule("D02", "本班任课教师值班且体信音美不参与", "hard"),
        _rule("D03", "值班同一时段教师不撞班", "hard"),
        _rule("D04", "同班同类值班每周最多一次", "hard"),
        _rule("D05", "早读静校正式课可用性", "hard", {"early": [1, 2], "quiet": [3, 4, 5, 6]}),
        _rule("D06", "单班教师三类值班各一次", "hard"),
        _rule("D07", "三次晚自习者白天值班最多3次", "hard"),
        _rule("D08", "多班教师白天值班精确2早读加2静校，最少例外", "hard"),
        _rule("D09", "早读静校各最多2次", "hard"),
        _rule("D10", "班主任和行政晚自习最多2次", "hard"),
        _rule("D11", "下午第5—8节满3节者不排晚自习", "hard"),
        _rule("D12", "不得连续3晚值班", "hard"),
        _rule("D13", "特殊班型晚自习学科", "conditional", None, required=True),
        _rule("D14", "周日晚自习是否固定班主任", "conditional", None, required=True),
        _rule("D15", "多班教师晚自习次数模式", "conditional", None, required=True),
        _rule("D16", "具名教师固定值班", "conditional", None, required=True),
        _rule("D17", "值班不计正式课课量", "hard"),
        _rule("D18", "值班只读取冻结正式课", "process"),
        _rule("O01", "值班工作量均衡", "soft"),
        _rule("O02", "晚自习优先当天有正式课", "soft"),
        _rule("O03", "避免连续两个晚上值班", "soft"),
        _rule("O04", "已删除：不作第3次晚自习人选偏好", "soft", False),
        _rule("O05", "避免同日多种值班", "soft"),
        _rule("O06", "早读静校尽量贴近相邻正式课", "soft"),
        _rule("G01", "正式课验证后冻结再排值班", "process"),
        _rule("G02", "硬约束双重验证", "process"),
        _rule("G03", "无解仅报最小放宽候选", "process"),
        _rule("G04", "输入和参考文件只读", "process"),
        _rule("G05", "固定四份Excel成品", "process"),
    ]
    return SchoolProfile({rule.id: rule for rule in rules})
