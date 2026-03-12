"""
ReviewerAgent — 需求质量审查代理

职责：
  对 SRS.md 执行多维度质量审查（二义性、一致性、完整性、可验证性、可追溯性等），
  输出结构化审查报告 issue_X.md。如无阻塞性问题则判定 APPROVED。
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime
from typing import Any, Optional, Tuple

from .base import BaseAgent

logger = logging.getLogger(__name__)

_DEFAULT_PROMPT_PATH = "backend/prompt/reviewer/system_prompt.txt"

# 审查报告中表示通过的标记
_APPROVED_MARKER = "**APPROVED**"


class ReviewerAgent(BaseAgent):
    """
    Requirements Quality Reviewer Agent.

    对外接口：
      process("review_SRS", srs_content=..., output_dir=..., round_number=...)
    返回 (issue_path, approved)。
    """

    def __init__(
        self,
        name: str = "Reviewer",
        prompt_path: str = _DEFAULT_PROMPT_PATH,
        config_path: Optional[str] = None,
        language: str = "en",
    ):
        super().__init__(name=name, prompt_path=prompt_path, config_path=config_path, language=language)

    # ------------------------------------------------------------------
    # 工具
    # ------------------------------------------------------------------

    @staticmethod
    def _write_file(path: str, content: str) -> None:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

    def _call_llm_fresh(self, user_prompt: str, max_tokens: Optional[int] = None) -> str:
        messages = [
            self.llm.format_message("system", self.system_prompt),
            self.llm.format_message("user", user_prompt + self.lang_reminder),
        ]
        return self.llm.generate(
            messages=messages,
            temperature=self.llm_params.get("temperature", 0.15),
            max_output_tokens=max_tokens or self.llm_params.get("max_output_tokens", 4096),
        ).strip()

    # ------------------------------------------------------------------
    # Action: review_SRS
    # ------------------------------------------------------------------

    def review_SRS(
        self,
        srs_content: str,
        output_dir: str,
        round_number: int = 1,
    ) -> Tuple[str, bool]:
        """
        审查 SRS.md，输出审查报告。

        Parameters
        ----------
        srs_content  : SRS.md 全文
        output_dir   : 输出目录
        round_number : 当前审查轮次（用于报告命名和标注）

        Returns
        -------
        Tuple[issue_path, approved]
            issue_path : 审查报告路径，e.g. .../issue_1.md
            approved   : 是否通过（True = 无阻塞性问题）
        """
        logger.info("[Reviewer] review_SRS: round %d — reviewing SRS...", round_number)

        prompt = (
            f"## SRS Under Review (Round {round_number})\n\n"
            f"{srs_content}\n\n"
            "## Task\n"
            "Perform a thorough quality review of the above SRS against ALL nine criteria "
            "(Correctness, Unambiguity, Completeness, Consistency, Verifiability, "
            "Traceability, Modifiability, Priority Ranking, Feasibility).\n\n"
            "Rules:\n"
            "- Report EVERY genuine issue you find.\n"
            "- For each issue, provide: section reference, severity "
            "(Critical / Major / Minor / Info), violated criterion, description, "
            "and a concrete recommendation.\n"
            "- If there are ZERO Critical or Major issues, set the Verdict to **APPROVED**.\n"
            "- Otherwise, set the Verdict to **REVISE** and state how many issues require attention.\n"
            f"- Use 'Round {round_number}' in the report heading.\n"
            "- Output the report in the format described in your system prompt."
        )

        report = self._call_llm_fresh(prompt, max_tokens=4096)

        # 清理 fence
        report = re.sub(r"^```[a-z]*\n?", "", report)
        report = re.sub(r"\n?```$", "", report).strip()

        # 判定是否 APPROVED
        approved = self._is_approved(report)

        # 写入 issue_X.md
        issue_filename = f"issue_{round_number}.md"
        issue_path = os.path.join(output_dir, issue_filename)
        self._write_file(issue_path, report)
        logger.info("[Reviewer] %s saved (approved=%s).", issue_filename, approved)

        verdict = "APPROVED ✓" if approved else f"REVISE — see {issue_filename}"
        print(f"[Reviewer] Round {round_number}: {verdict}")

        return issue_path, approved

    @staticmethod
    def _is_approved(report: str) -> bool:
        """
        分析审查报告判定是否通过。

        检测逻辑（宽容匹配）：
          1. 报告 Verdict 部分显式包含 **APPROVED**
          2. 且不包含 **REVISE**
        """
        # 尝试提取 Verdict 段落
        verdict_match = re.search(
            r"##\s*Verdict\s*\n(.*?)(?:\n##|\Z)", report, re.DOTALL | re.IGNORECASE
        )
        if verdict_match:
            verdict_text = verdict_match.group(1)
            if "**APPROVED**" in verdict_text and "**REVISE**" not in verdict_text:
                return True
            return False

        # 未找到 Verdict 段落，从全文判断
        has_approved = _APPROVED_MARKER in report
        has_revise = "**REVISE**" in report
        if has_approved and not has_revise:
            return True
        return False

    # ------------------------------------------------------------------
    # BaseAgent 抽象方法
    # ------------------------------------------------------------------

    def process(self, action: str, **kwargs) -> Any:
        """
        支持的 action：
          - "review_SRS" — 审查 SRS 并生成 issue 报告
        """
        logger.info("[%s] >>> Executing action: %s", self.agent_name, action)
        if action == "review_SRS":
            return self.review_SRS(
                srs_content=kwargs["srs_content"],
                output_dir=kwargs["output_dir"],
                round_number=kwargs.get("round_number", 1),
            )
        else:
            raise ValueError(f"ReviewerAgent: unknown action '{action}'")
