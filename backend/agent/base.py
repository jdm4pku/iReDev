from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, List
import os
from pathlib import Path

from ..llm.factory import LLMFactory
from ..llm.base import BaseLLM

class BaseAgent(ABC):
    """Base class for all agents in the iReDev framework."""

    # Language instruction templates — appended to system prompt when language is set
    _LANG_INSTRUCTIONS = {
        "zh": (
            "\n\n[语言要求] 你必须始终使用中文进行所有输出。"
            "所有生成的文档内容、分析结果、访谈问题、回答和任何文本输出都必须使用中文。"
            "即使输入内容包含英文，你的输出也必须完全使用中文。"
            "文档中的专业术语可以在首次出现时用括号标注英文原文，例如：业务需求文档（BRD）。"
        ),
        "en": (
            "\n\n[Language Requirement] You MUST always produce ALL output in English. "
            "All generated document content, analysis results, interview questions, answers, "
            "and any textual output MUST be in English. "
            "Even if the input contains non-English text, your output MUST be entirely in English."
        ),
    }

    def __init__(self, name:str, prompt_path:str, config_path: Optional[str] = None, language: str = "en"):
        """Initialize the base agent.
        
        Args:
            name: Agent name
            prompt_path: Path to the system prompt file
            config_path: Optional path to the LLM configuration file
            language: Output language - "zh" for Chinese, "en" for English
        """
        self.agent_name = name
        self.prompt_path = prompt_path
        self.language = language
        self.system_prompt = self._get_system_prompt(prompt_path)
        # Append language instruction to system prompt
        lang_instruction = self._LANG_INSTRUCTIONS.get(language, "")
        if lang_instruction:
            self.system_prompt += lang_instruction
        self._memory: list[Dict[str,Any]] = []
        self._monitor: list[Dict[str,Any]] = [] # monitor which generated requirements artifacts
        self._action_list: list[Dict[str,Any]] = [] # which actions can be done
        self._knowledge_base: list[Dict[str,Any]] = [] # store knowledge to support this agent
        self.llm, self.llm_params = self._initialize_llm(agent_name=name, config_path=config_path)
        self.add_to_memory("system", self.system_prompt)


    def _initialize_llm(self, agent_name: str, config_path: Optional[str] = None) -> tuple[BaseLLM, Dict[str, Any]]:
        """Initialize the LLM for this agent

        Args:
            agent_name: Name of the agent
            config_path: Optional path to the configuration file. If None, uses default path.
        """
        if config_path is None:
            config_path = str(Path(__file__).parent.parent / "config" / "config.yaml")
            print(f"Using default config from {config_path}")
        
        config = LLMFactory.load_config(config_path)

        # check for agent-specific config
        agent_config = config.get("agent_llms",{}).get(agent_name.lower())

        # use agent-specific config if available, otherwise use general llm config
        llm_config = agent_config if agent_config else config.get("llm", {})

        # verify api_key is provided in config
        if ("api_key" not in llm_config or not llm_config["api_key"]) and (llm_config["type"] not in ["huggingface", "local"]):
            raise ValueError(f"API key is required for LLM type '{llm_config['type']}' in config file {config_path}")
        
        # Extract LLM parameters
        llm_params = {
            "max_output_tokens": llm_config.get("max_output_tokens", 4096),
            "temperature": llm_config.get("temperature", 0.1),
            "model": llm_config.get("model")
        }

        return LLMFactory.create_llm(llm_config, config_path=config_path), llm_params
    
    def _get_system_prompt(self, prompt_path: Optional[str] = None) -> str:
        path = prompt_path or self.prompt_path
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    

    def generate_response(self, messages: Optional[List[Dict[str, Any]]] = None) -> str:
        """Generate a response using the agent's LLM and memory"""
        result = self.llm.generate(
            messages = messages if messages is not None else self._memory,
            temperature=self.llm_params.get("temperature", 0.1),
            max_output_tokens=self.llm_params.get("max_output_tokens", 4096)
        )
        return result or ""

    # Language reminder — short suffix appended to user prompts to reinforce output language
    _LANG_REMINDERS = {
        "zh": "\n\n[重要：请务必使用中文输出所有内容。]",
        "en": "\n\n[IMPORTANT: You MUST respond entirely in English.]",
    }

    @property
    def lang_reminder(self) -> str:
        """返回当前语言对应的简短提醒文本，可附加到任何 user prompt 末尾。"""
        return self._LANG_REMINDERS.get(self.language, "")

    def add_to_memory(self, role: str, content: str) -> None:
        """Add a message to the agent's memory.
        
        Args:
            role: The role of the message sender (e.g., 'system', 'user', 'assistant')
            content: The content of the message
        """
        assert content is not None and content != "", "Content cannot be empty"
        self._memory.append(self.llm.format_message(role, content))

    def refresh_memory(self, new_memory: list[Dict[str, Any]]) -> None:
        """Replace the current memory with new memory.
        
        Args:
            new_memory: The new memory to replace the current memory
        """
        self._memory = [
            self.llm.format_message(msg["role"], msg["content"])
            for msg in new_memory
        ]
    
    def clear_memory(self) -> None:
        """Clear the agent's memory."""
        self._memory = []
    
    @property
    def memory(self) -> list[Dict[str, Any]]:
        """Get the agent's memory.
        
        Returns:
            The agent's memory as a list of message dictionaries
        """
        return self._memory.copy()
    
    # TODO
    def _think(self):
        pass

    @abstractmethod
    def process(self, action):
        """Execute the action decided by the agent.
        
        This method should be implemented by each specific agent
        """
        pass
    








        