"""
KnowledgeLoader — 需求工程知识库加载与检索工具

从 YAML 知识库文件中加载条目，支持按 agent 名称和流程阶段过滤，
输出格式化的提示词片段供 Agent 注入 LLM 上下文。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import yaml

logger = logging.getLogger(__name__)

_DEFAULT_KB_PATH = str(
    Path(__file__).parent.parent / "knowledge" / "base" / "requirements_engineering_kb.yaml"
)

# action → phase 映射（由各 Agent 的 process() 定义推导）
ACTION_PHASE_MAP: Dict[str, List[str]] = {
    # Interviewer actions
    "interview_with_customer": ["elicitation"],
    "generate_BRD":            ["analysis", "specification"],
    "capture_persona":         ["analysis"],
    "interview_with_enduser":  ["elicitation"],
    "write_userRD":            ["specification"],
    # Analyst actions
    "requirements_modeling":   ["analysis"],
    "requirements_analysis":   ["analysis", "specification"],
    # Archivist actions
    "write_SRS":               ["specification"],
    "revise_SRS":              ["specification", "validation"],
    # Reviewer actions
    "review_SRS":              ["validation"],
}


class KnowledgeLoader:
    """加载并检索需求工程知识库条目。"""

    def __init__(self, kb_path: str = _DEFAULT_KB_PATH):
        self._kb_path = kb_path
        self._entries: List[Dict[str, Any]] = []
        self._loaded = False

    # ------------------------------------------------------------------
    # 加载
    # ------------------------------------------------------------------

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        if not os.path.exists(self._kb_path):
            logger.warning("[KnowledgeLoader] KB file not found: %s", self._kb_path)
            self._loaded = True
            return
        try:
            with open(self._kb_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            if not data:
                self._loaded = True
                return
            # 将三个分类合并为统一列表
            for section_key in ("domain_knowledge", "typical_methodologies", "common_strategies"):
                items = data.get(section_key, [])
                if items:
                    self._entries.extend(items)
            logger.info("[KnowledgeLoader] Loaded %d KB entries from %s", len(self._entries), self._kb_path)
        except Exception as exc:
            logger.warning("[KnowledgeLoader] Failed to load KB: %s", exc)
        self._loaded = True

    # ------------------------------------------------------------------
    # 检索
    # ------------------------------------------------------------------

    def query(
        self,
        agent_name: str,
        phases: Optional[List[str]] = None,
        action: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        按 agent 名称和阶段过滤知识库条目。

        Parameters
        ----------
        agent_name : agent 的名称（如 "interviewer", "analyst"）
        phases     : 显式指定阶段列表，如 ["elicitation", "analysis"]
        action     : 如果提供，自动推导阶段（优先级低于 phases）

        Returns
        -------
        匹配条目列表
        """
        self._ensure_loaded()

        agent_lower = agent_name.lower()

        # 确定目标阶段集合
        target_phases: Set[str] = set()
        if phases:
            target_phases = set(phases)
        elif action and action in ACTION_PHASE_MAP:
            target_phases = set(ACTION_PHASE_MAP[action])

        matched: List[Dict[str, Any]] = []
        for entry in self._entries:
            # 检查 target_agent 是否匹配
            target_agents = entry.get("target_agent", [])
            if agent_lower not in [a.lower() for a in target_agents]:
                continue

            # 如果指定了阶段，检查 applicable_phase 交集
            if target_phases:
                entry_phases = set(entry.get("applicable_phase", []))
                if not target_phases & entry_phases:
                    continue

            matched.append(entry)

        return matched

    # ------------------------------------------------------------------
    # 格式化
    # ------------------------------------------------------------------

    @staticmethod
    def format_as_prompt(
        entries: List[Dict[str, Any]],
        language: str = "en",
    ) -> str:
        """
        将知识库条目格式化为可注入 LLM 提示词的文本块。

        Parameters
        ----------
        entries  : query() 返回的条目列表
        language : "zh" 或 "en"，决定标题语言

        Returns
        -------
        格式化的 Markdown 文本（如果无条目则返回空字符串）
        """
        if not entries:
            return ""

        if language == "zh":
            header = "## 需求工程知识指引\n\n以下是与当前任务相关的需求工程知识，请在执行任务时参考并遵循：\n"
        else:
            header = (
                "## Requirements Engineering Knowledge Guidelines\n\n"
                "The following RE knowledge entries are relevant to your current task. "
                "Apply them as guiding principles:\n"
            )

        lines = [header]
        for entry in entries:
            entry_id = entry.get("id", "?")
            name = entry.get("name", "")
            content = entry.get("normalized_content", "").strip()
            trigger = entry.get("trigger", "")
            expected_use = entry.get("expected_use", "")

            lines.append(f"### [{entry_id}] {name}\n")
            lines.append(f"{content}\n")
            if trigger:
                if language == "zh":
                    lines.append(f"- **触发条件**: {trigger}")
                else:
                    lines.append(f"- **When to apply**: {trigger}")
            if expected_use:
                if language == "zh":
                    lines.append(f"- **应用方式**: {expected_use}")
                else:
                    lines.append(f"- **How to apply**: {expected_use}")
            lines.append("")

        return "\n".join(lines)
