#!/usr/bin/env python3
"""
iReDev Demo — 需求开发全流程演示

用法:
    cd /Users/dongming/Desktop/iReDev
    python3 run_demo.py
"""

import os
import sys

# 确保项目根目录在 sys.path 中
ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from backend.iReqDev import iReqDevTeam

CONFIG_PATH = os.path.join(ROOT, "backend", "config", "config.yaml")
OUTPUT_DIR  = os.path.join(ROOT, "output")

def main():
    # 确保输出目录存在
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    print("=" * 60)
    print("  iReDev — 多Agent需求开发框架 Demo")
    print("=" * 60)
    print(f"  Config : {CONFIG_PATH}")
    print(f"  Output : {OUTPUT_DIR}")
    print(f"  Human-in-loop : False")
    print("=" * 60)
    print()

    team = iReqDevTeam(
        project_name="Demo Project",
        output_dir=OUTPUT_DIR,
        config_path=CONFIG_PATH,
        human_in_loop=False,
        max_review_rounds=2,
        language="zh",  # "zh" for Chinese, "en" for English
    )
    team.run()

if __name__ == "__main__":
    main()
