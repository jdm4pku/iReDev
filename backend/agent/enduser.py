"""
EndUserAgent — LLM 驱动的终端用户角色代理

职责：
  - 代表一种特定的系统终端用户（角色/画像）接受访谈
  - 基于角色描述和已有对话，用 LLM 生成符合人设的回答
  - 不涉及人工交互（纯 LLM，不同于 HumanCustomerAgent）
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from .base import BaseAgent

logger = logging.getLogger(__name__)

_DEFAULT_PROMPT_PATH = "backend/prompt/enduser/system_prompt.txt"


class EndUserAgent(BaseAgent):
    """
    LLM-simulated end-user persona agent.

    每个实例代表 UserList.md 中的一种终端用户角色，在访谈中扮演该角色回答
    InterviewerAgent 的问题。
    """

    def __init__(
        self,
        persona_name: str,
        persona_description: str,
        config_path: Optional[str] = None,
        prompt_path: str = _DEFAULT_PROMPT_PATH,
        name: Optional[str] = None,
        language: str = "en",
        project_context: str = "",
    ):
        """
        Parameters
        ----------
        persona_name:
            角色名称，如 "Administrator"、"Regular User"。
        persona_description:
            角色的详细描述（来自 UserList.md 的 Description 列）。
        config_path:
            LLM 配置文件路径。
        prompt_path:
            系统提示词模版路径（含 {PERSONA_NAME} / {PERSONA_DESCRIPTION} / {PROJECT_CONTEXT} 占位符）。
        name:
            Agent 名称（默认为 persona_name）。
        language:
            输出语言 - "zh" 中文, "en" 英文
        project_context:
            项目/系统背景信息（如 BRD 摘要），让 EndUser 了解正在讨论的系统。
        """
        # 在调用 super().__init__ 之前，先准备个性化的 system_prompt
        # BaseAgent.__init__ 会调用 _get_system_prompt，但我们需要先读取模版再替换
        self.persona_name = persona_name
        self.persona_description = persona_description
        self.project_context = project_context
        self._personalized_prompt: Optional[str] = None

        # 使用父类初始化，传入模版路径；base 会读文件写入 memory
        agent_name = name or f"EndUser_{persona_name.replace(' ', '_')}"
        super().__init__(
            name=agent_name,
            prompt_path=prompt_path,
            config_path=config_path,
            language=language,
        )

        # 用个性化 system_prompt 替换 base 里写入的通用模版
        self._apply_persona_to_memory()

    # ------------------------------------------------------------------
    # 角色个性化
    # ------------------------------------------------------------------

    def _get_system_prompt(self, prompt_path: Optional[str] = None) -> str:
        """Override: 读取模版后替换 persona 占位符。"""
        path = prompt_path or self.prompt_path
        with open(path, "r", encoding="utf-8") as f:
            template = f.read()
        # 在 super().__init__ 调用前 persona 字段可能未设置，安全降级
        pname = getattr(self, "persona_name", "Unknown Role")
        pdesc = getattr(self, "persona_description", "No description provided.")
        pctx = getattr(self, "project_context", "")
        return (
            template
            .replace("{PERSONA_NAME}", pname)
            .replace("{PERSONA_DESCRIPTION}", pdesc)
            .replace("{PROJECT_CONTEXT}", pctx or "No project context provided.")
        )

    def _apply_persona_to_memory(self) -> None:
        """
        将 memory[0]（system 消息）替换为个性化后的 system_prompt。
        系统消息是 base.__init__ 写入的，此时 persona 字段已确定，重新注入。
        """
        personalized = self.system_prompt  # 已由 _get_system_prompt 生成
        if self._memory and self._memory[0].get("role") == "system":
            self._memory[0] = self.llm.format_message("system", personalized)
        else:
            # 找不到就插在最前面
            self._memory.insert(0, self.llm.format_message("system", personalized))
        logger.debug(
            "[EndUserAgent] Persona applied: %s", self.persona_name
        )

    # ------------------------------------------------------------------
    # 核心接口
    # ------------------------------------------------------------------

    def answer(self, question: str) -> str:
        """
        接收访谈问题，生成符合该角色人设的回答。

        Parameters
        ----------
        question:
            InterviewerAgent 的当前问题文本。

        Returns
        -------
        str
            该角色的回答。
        """
        self.add_to_memory("user", f"[Interviewer Question]\n{question}")
        response = self.generate_response()
        response = response.strip()
        self.add_to_memory("assistant", response)
        logger.info("[EndUserAgent:%s] Q: %s…  A: %s…", self.persona_name, question[:50], response[:60])
        return response

    # ------------------------------------------------------------------
    # BaseAgent 抽象方法
    # ------------------------------------------------------------------

    def process(self, action: str, **kwargs) -> Any:
        logger.info("[%s] >>> Executing action: %s", self.agent_name, action)
        if action == "answer":
            return self.answer(question=kwargs["question"])
        raise ValueError(f"EndUserAgent: unknown action '{action}'")

    # ------------------------------------------------------------------
    # 便捷属性
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return f"EndUserAgent(persona={self.persona_name!r})"
