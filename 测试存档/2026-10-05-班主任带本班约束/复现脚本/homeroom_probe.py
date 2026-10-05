# -*- coding: utf-8 -*-
"""验证 H29「班主任须在自己带的班上任课」开关。

用法（仓库任意位置均可）：
    python homeroom_probe.py            # 开关关 / 开 对比（只到建模层，秒级，不求解）

只读仓库、只在系统临时目录写入。用仓库自带合成数据 engine/test_input_25_new/。
"""
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
ENGINE_DIR = os.path.join(ROOT, "engine")
sys.path.insert(0, ENGINE_DIR)

SAMPLE = os.path.join(ENGINE_DIR, "test_input_25_new", "input.xlsx")


def run(tag, on):
    from scheduler.data.load import load_problem
    from scheduler.solver.model import build_model

    d = tempfile.mkdtemp(prefix="paike_hm_")
    try:
        shutil.copy(SAMPLE, os.path.join(d, "input.xlsx"))
        with open(os.path.join(d, "hard_limits.json"), "w", encoding="utf-8") as f:
            json.dump({"classes": {"homeroom_must_teach_own": bool(on)}}, f)
        p = load_problem(d)
        b = build_model(p)
        print("=" * 12, tag, "=" * 12)
        print("  cfg.homeroom_must_teach_own =", p.config.homeroom_must_teach_own)
        print("  有班主任的班:", sum(1 for c in p.classes if c.homeroom), "/", len(p.classes))
        print("  约束数:", len(b.model.Proto().constraints))
        for w in p.warnings + b.warnings:
            if "班主任" in w:
                print("  W:", w)
    finally:
        shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    if not os.path.exists(SAMPLE):
        print("[skip] 没找到合成数据", SAMPLE)
        raise SystemExit(1)
    run("开关关（默认）", False)
    run("开关开", True)
