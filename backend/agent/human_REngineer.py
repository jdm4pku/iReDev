"""
HumanREngineerAgent — 人在回路的需求工程师代理

职责：
  当关键制品（BRD.md, UserList.md, SyRS.md, SRS.md）生成后，
  提示真人查看制品并收集修改意见。
  本 Agent 不调用 LLM，不继承 BaseAgent。
  修改工作由生成该制品的原 Agent 完成。

使用方式（由 iReqDev 编排器调用）：
    feedback = human_re.collect_feedback(artifact_name, artifact_path)
    has_more = human_re.ask_has_more_feedback(artifact_name)
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# 用户输入这些内容视为"无意见，通过"
_APPROVE_KEYWORDS = {
    "", "ok", "OK", "pass", "PASS", "lgtm", "LGTM",
    "通过", "没有意见", "没意见", "无",
}


class HumanREngineerAgent:
    """
    Human-in-the-loop Requirements Engineer.

    不继承 BaseAgent，不调用 LLM。
    仅负责终端交互：提示查看制品路径、收集反馈文本、确认 Yes/No。
    """

    def __init__(self, max_feedback_rounds: int = 5):
        self.max_feedback_rounds = max_feedback_rounds

    # ------------------------------------------------------------------
    # 收集反馈
    # ------------------------------------------------------------------

    def collect_feedback(self, artifact_name: str, artifact_path: str) -> Optional[str]:
        """
        提示人工查看制品并收集修改意见。

        Parameters
        ----------
        artifact_name : 制品文件名，如 "BRD.md"
        artifact_path : 制品文件的完整路径

        Returns
        -------
        str | None
            修改意见文本；None 表示没有意见（通过）。
        """
        print(f"\n{'='*60}")
        print(f"  📄 关键制品已生成: {artifact_name}")
        print(f"  📁 请查看: {artifact_path}")
        print(f"{'='*60}")
        print(f"请审查 {artifact_name} 并输入修改意见。")
        print("  - 直接回车表示没有意见，继续后续流程")
        print("  - 支持多行输入：连续两次回车结束输入")
        print()

        lines: list[str] = []
        empty_count = 0
        while True:
            try:
                line = input("> " if not lines else "  ")
            except EOFError:
                break

            if line.strip() == "":
                empty_count += 1
                if empty_count >= 2 or not lines:
                    break
                lines.append("")
            else:
                empty_count = 0
                lines.append(line)

        raw = "\n".join(lines).strip()

        if raw in _APPROVE_KEYWORDS:
            return None
        return raw

    # ------------------------------------------------------------------
    # 修订后确认
    # ------------------------------------------------------------------

    def ask_has_more_feedback(self, artifact_name: str) -> bool:
        """
        修订完成后询问是否还有意见。

        Returns
        -------
        bool — True 表示还有意见，需继续修改；False 表示通过。
        """
        print(f"\n[HumanRE] ✓ {artifact_name} 修改已完成，请重新查看。")
        while True:
            choice = input("还有意见吗？(Yes/No): ").strip().lower()
            if choice in ("no", "n", "否", "没有", "没了", ""):
                return False
            elif choice in ("yes", "y", "是", "有"):
                return True
            else:
                print("请输入 Yes 或 No。")
