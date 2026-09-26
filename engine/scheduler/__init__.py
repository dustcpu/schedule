# -*- coding: utf-8 -*-
"""排课决策内核（算法团队交付物）。

模块分层（参考 Course-Sorting-Algorithm 的 data/solver/export 分层思路）：
  config        配置：作息、固定课、连堂、软约束权重、求解参数
  data.load     读 input.xlsx / hard_limits.json / requirements.txt
  data.validate 求解前校验（缺字段、课时超额、教师缺失等）
  solver.model  CP-SAT 建模（硬约束）
  solver.solve  求解 + 多解 + 评分
  export        写 result.xlsx / result.pdf / status.json
"""
__version__ = "0.1.5"
