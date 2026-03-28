"""
HumanCustomerAgent — 人在回路的业务客户代理

职责：
  - 接收来自 InterviewerAgent 的每轮访谈问题
  - 借助 LLM 生成 2 个候选回答供真人参考/选择
  - 支持真人直接输入自定义回复（Human-in-the-Loop）
  - 将最终选定的回复返回给 InterviewerAgent
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from .base import BaseAgent

logger = logging.getLogger(__name__)

_DEFAULT_PROMPT_PATH = "backend/prompt/customer/system_prompt.txt"


class HumanCustomerAgent(BaseAgent):
    """
    Human-in-the-loop customer agent.

    在每轮对话中：
    1. 将访谈问题加入 memory
    2. 调用 LLM 生成 2 个候选回答（JSON 格式）
    3. 在终端展示候选回答，等待用户选择或自由输入
    4. 将用户最终回复返回，并追加到自身 memory
    """

    def __init__(
        self,
        name: str = "HumanCustomer",
        prompt_path: str = _DEFAULT_PROMPT_PATH,
        config_path: Optional[str] = None,
        language: str = "en",
    ):
        super().__init__(name=name, prompt_path=prompt_path, config_path=config_path, language=language)
        # customer memory 从 system_prompt 开始（base.__init__ 已 add_to_memory("system", ...)）
        self._project_context_injected = False

    # ------------------------------------------------------------------
    # 项目上下文注入
    # ------------------------------------------------------------------

    def inject_project_context(self, project_description: str) -> None:
        """
        将用户的项目描述注入到 Customer 的 system prompt 和 memory 中，
        使后续 LLM 生成候选回答时能紧扣项目主题。

        应在 interview_with_customer 开始时调用一次。
        """
        if self._project_context_injected:
            return
        self._project_context_injected = True

        # 替换 system prompt 中的 {PROJECT_CONTEXT} 占位符
        updated_prompt = self.system_prompt.replace(
            "{PROJECT_CONTEXT}",
            project_description or "No project description provided."
        )

        # 更新 memory 中的 system 消息
        if self._memory and self._memory[0].get("role") == "system":
            self._memory[0] = self.llm.format_message("system", updated_prompt)
        else:
            self._memory.insert(0, self.llm.format_message("system", updated_prompt))

        # 额外追加一条 assistant 消息，表明 Customer 已知自己的项目
        self.add_to_memory(
            "assistant",
            f"I have a project idea I'd like to discuss. Here is my project description:\n\n{project_description}"
        )
        logger.info("[HumanCustomer] Project context injected (%d chars).", len(project_description))

    # ------------------------------------------------------------------
    # 核心接口
    # ------------------------------------------------------------------

    def answer(self, question: Optional[str] = None) -> str:
        """
        接收一个访谈问题，返回用户最终确认的回复。

        Parameters
        ----------
        question:
            InterviewerAgent 当前轮次的问题文本。若为 None，则直接等待用户输入
            （用于首次描述项目场景）。

        Returns
        -------
        str
            用户最终回复（可能是候选答案之一，也可能是自由输入）。
        """
        if question is None:
            # 首次调用：直接让人输入项目描述，不需要 LLM 生成候选
            return self._human_input_only(
                prompt="请输入您的项目描述（简洁概括即可）：\n> "
            )

        # 将访谈问题加入 memory（role=user，代表 interviewer 提问）
        self.add_to_memory("user", f"[Interviewer Question]\n{question}")

        # 生成候选回答
        try:
            candidates = self._generate_candidates()
        except Exception as exc:
            logger.warning("Candidate generation failed: %s", exc)
            candidates = {}

        # 展示候选并等待用户选择
        final_reply = self._present_and_select(question, candidates)

        # 将最终回复追加进 memory（role=assistant，代表 customer 回复）
        self.add_to_memory("assistant", final_reply)
        return final_reply

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------

    def _generate_candidates(self) -> Dict[str, str]:
        """
        调用 LLM，基于当前 memory（含对话历史 + 当前问题）生成 2 个候选回答。
        返回形如 {"candidate_1": "...", "candidate_2": "..."} 的字典。
        若解析失败，返回空字典，降级到纯人工输入。
        """
        # 临时追加提示，引导 LLM 输出 JSON
        generation_prompt = (
            "Based on the conversation so far and the latest interviewer question, "
            "generate two candidate responses for the customer. "
            "Output ONLY valid JSON with keys 'candidate_1' and 'candidate_2'."
            f"{self.lang_reminder}"
        )
        messages = self._memory + [self.llm.format_message("user", generation_prompt)]

        raw = self.llm.generate(
            messages=messages,
            temperature=self.llm_params.get("temperature", 0.7),
            max_output_tokens=self.llm_params.get("max_output_tokens", 1024),
        )

        # 解析 JSON（容错处理 markdown code-fence）
        text = (raw or "").strip()
        if not text:
            logger.warning("LLM returned empty response for candidate generation.")
            return {}
        if text.startswith("```"):
            lines = text.splitlines()
            text = "\n".join(
                l for l in lines if not l.startswith("```")
            ).strip()

        try:
            parsed = json.loads(text)
            if "candidate_1" in parsed and "candidate_2" in parsed:
                return parsed
        except json.JSONDecodeError:
            logger.warning("Failed to parse candidate JSON, falling back to manual input.\nRaw: %s", text[:200])
        except Exception as exc:
            logger.warning("Candidate generation error: %s", exc)

        return {}

    def _present_and_select(self, question: str, candidates: Dict[str, str]) -> str:
        """在终端中展示候选回答，等待用户操作，返回最终回复。"""
        print("\n" + "=" * 60)
        print(f"[Interviewer] {question}")
        print("-" * 60)

        if candidates:
            print("[候选回答 - 由 AI 辅助生成，供参考]")
            print(f"\n  [1] {candidates.get('candidate_1', '')}")
            print(f"\n  [2] {candidates.get('candidate_2', '')}")
            print("\n  [3] 自定义回复（直接输入）")
            print("-" * 60)

            while True:
                choice = input("请选择 [1/2/3]：").strip()
                if choice == "1":
                    return candidates["candidate_1"]
                elif choice == "2":
                    return candidates["candidate_2"]
                elif choice == "3" or choice == "":
                    break
                else:
                    print("请输入 1、2 或 3。")
        else:
            print("[AI 候选回答生成失败，请直接输入]")

        return self._human_input_only(prompt="您的回复：\n> ")

    @staticmethod
    def _human_input_only(prompt: str) -> str:
        """纯人工输入，直到输入非空为止。"""
        while True:
            reply = input(prompt).strip()
            if reply:
                return reply
            print("回复不能为空，请重新输入。")

    # ------------------------------------------------------------------
    # BaseAgent 抽象方法
    # ------------------------------------------------------------------

    def process(self, action: str, **kwargs) -> Any:
        """
        Human customer agent 只响应 'answer' 动作。
        """
        logger.info("[%s] >>> Executing action: %s", self.agent_name, action)
        if action == "answer":
            return self.answer(question=kwargs.get("question"))
        raise ValueError(f"HumanCustomerAgent: unknown action '{action}'")
