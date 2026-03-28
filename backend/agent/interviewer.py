"""
InterviewerAgent — 需求访谈代理

职责（由制品池事件触发）：
  1. customer_project_description.md 新建 → interview_with_customer
  2. customer_dialogue.md 新建           → generate_BRD
  3. BRD.md 新建                         → capture_persona
  4. UserList.md 新建                    → interview_with_enduser
  5. enduser_dialogue.md 新建            → write_userRD
"""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.request
import zlib
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from .base import BaseAgent
from .human_customer import HumanCustomerAgent
from .enduser import EndUserAgent

logger = logging.getLogger(__name__)

_BRD_TEMPLATE_PATH = "backend/knowledge/BRD_template.md"
_BRD_TEMPLATE_PATH_ZH = "backend/knowledge/BRD_template_zh.md"
_USERLIST_TEMPLATE_PATH = "backend/knowledge/UserList_template.md"
_USERLIST_TEMPLATE_PATH_ZH = "backend/knowledge/UserList_template_zh.md"
_USERRD_TEMPLATE_PATH = "backend/knowledge/UserRD_template.md"
_USERRD_TEMPLATE_PATH_ZH = "backend/knowledge/UserRD_template_zh.md"
_DEFAULT_PROMPT_PATH = "backend/prompt/interviewer/system_prompt.txt"
_ENDUSER_Q_PROMPT_PATH = "backend/prompt/interviewer/enduser_question_prompt.txt"
_PLANTUML_API = "https://www.plantuml.com/plantuml/png/"
_DONE_MARKER = "[DONE]"
_PLANTUML_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz-_"


def _plantuml_encode(text: str) -> str:
    """将 PlantUML 文本压缩并编码为 PlantUML 公共 API 可接受的 URL 片段。"""
    compressed = zlib.compress(text.encode("utf-8"), 9)[2:-4]  # strip zlib header + adler32
    result = []
    data = compressed
    i = 0
    while i < len(data):
        b1 = data[i]
        b2 = data[i + 1] if i + 1 < len(data) else 0
        b3 = data[i + 2] if i + 2 < len(data) else 0
        result.append(_PLANTUML_CHARS[(b1 >> 2) & 0x3F])
        result.append(_PLANTUML_CHARS[((b1 & 0x3) << 4) | ((b2 >> 4) & 0xF)])
        result.append(_PLANTUML_CHARS[((b2 & 0xF) << 2) | ((b3 >> 6) & 0x3)])
        result.append(_PLANTUML_CHARS[b3 & 0x3F])
        i += 3
    return "".join(result)


class InterviewerAgent(BaseAgent):
    """
    Requirements Interviewer Agent.

    触发方式（由 iReqDev 的制品池订阅调用 process()）：
      - process("interview_with_customer", ...)
      - process("generate_BRD", ...)
      - process("capture_persona", ...)
      - process("interview_with_enduser", ...)
      - process("write_userRD", ...)
    """

    def __init__(
        self,
        name: str = "Interviewer",
        prompt_path: str = _DEFAULT_PROMPT_PATH,
        config_path: Optional[str] = None,
        brd_template_path: str = _BRD_TEMPLATE_PATH,
        userlist_template_path: str = _USERLIST_TEMPLATE_PATH,
        userrd_template_path: str = _USERRD_TEMPLATE_PATH,
        max_turns: int = 10,
        max_turns_per_enduser: int = 8,
        language: str = "en",
    ):
        """
        Parameters
        ----------
        name: agent 名称
        prompt_path: interviewer system prompt 路径
        config_path: LLM 配置文件路径
        brd_template_path: BRD 模版文件路径
        userlist_template_path: UserList 模版文件路径
        userrd_template_path: UserRD 模版文件路径
        max_turns: 客户访谈最大对话轮数
        max_turns_per_enduser: 每个 enduser 访谈最大对话轮数
        language: 输出语言 - "zh" 中文, "en" 英文
        """
        super().__init__(name=name, prompt_path=prompt_path, config_path=config_path, language=language)
        # Select template paths based on language
        if language == "zh":
            self.brd_template_path = _BRD_TEMPLATE_PATH_ZH
            self.userlist_template_path = _USERLIST_TEMPLATE_PATH_ZH
            self.userrd_template_path = _USERRD_TEMPLATE_PATH_ZH
        else:
            self.brd_template_path = brd_template_path
            self.userlist_template_path = userlist_template_path
            self.userrd_template_path = userrd_template_path
        self.max_turns = max_turns
        self.max_turns_per_enduser = max_turns_per_enduser
        # 缓存模版内容（延迟加载）
        self._brd_template: Optional[str] = None
        self._userlist_template: Optional[str] = None
        self._userrd_template: Optional[str] = None
        self._enduser_q_prompt: Optional[str] = None

    # ------------------------------------------------------------------
    # 属性 / 工具
    # ------------------------------------------------------------------

    @property
    def brd_template(self) -> str:
        """懒加载 BRD 模版内容。"""
        if self._brd_template is None:
            with open(self.brd_template_path, "r", encoding="utf-8") as f:
                self._brd_template = f.read()
        return self._brd_template

    @property
    def userlist_template(self) -> str:
        """懒加载 UserList 模版内容。"""
        if self._userlist_template is None:
            with open(self.userlist_template_path, "r", encoding="utf-8") as f:
                self._userlist_template = f.read()
        return self._userlist_template

    @property
    def userrd_template(self) -> str:
        """懒加载 UserRD 模版内容。"""
        if self._userrd_template is None:
            with open(self.userrd_template_path, "r", encoding="utf-8") as f:
                self._userrd_template = f.read()
        return self._userrd_template

    @property
    def enduser_q_prompt(self) -> str:
        """懒加载 enduser 访谈问题生成 prompt 模版。"""
        if self._enduser_q_prompt is None:
            with open(_ENDUSER_Q_PROMPT_PATH, "r", encoding="utf-8") as f:
                self._enduser_q_prompt = f.read()
        return self._enduser_q_prompt

    def _read_file(self, path: str) -> str:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()

    def _write_file(self, path: str, content: str) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

    # ------------------------------------------------------------------
    # Action 1: interview_with_customer
    # ------------------------------------------------------------------

    def interview_with_customer(
        self,
        project_description: str,
        human_customer: HumanCustomerAgent,
        output_dir: str,
        max_turns: Optional[int] = None,
    ) -> str:
        """
        与 HumanCustomerAgent 完成多轮访谈，保存对话记录，返回保存路径。

        Parameters
        ----------
        project_description:
            customer_project_description.md 的文本内容
        human_customer:
            HumanCustomerAgent 实例
        output_dir:
            制品输出目录
        max_turns:
            本次访谈最大轮数（默认使用 self.max_turns）
        """
        turns = max_turns or self.max_turns
        logger.info("[Interviewer] Starting interview for project: %s...", project_description[:80])

        # ── Step 0: 将项目描述注入 Customer 的上下文 ──────────────────────
        human_customer.inject_project_context(project_description)

        # ── Step 1: 理解 BRD 模版，生成访谈问题清单 ──────────────────────
        question_list = self._generate_question_list(project_description)
        logger.info("[Interviewer] Generated question list:\n%s", question_list)

        # Memory 刷新：保留 system_prompt + 问题清单摘要，为对话循环做准备
        self.refresh_memory([
            {"role": "system", "content": self.system_prompt},
            {
                "role": "assistant",
                "content": (
                    f"I have analyzed the BRD template and the project description. "
                    f"Here is my question list to guide the interview:\n\n{question_list}"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Project description from customer:\n{project_description}\n\n"
                    "Please start the interview."
                ),
            },
        ])

        # ── Step 2: 多轮对话 ──────────────────────────────────────────────
        dialogue_history: List[Dict[str, str]] = [
            {"role": "customer_description", "content": project_description}
        ]

        for turn_idx in range(1, turns + 1):
            logger.info("[Interviewer] Turn %d / %d", turn_idx, turns)

            # 生成本轮访谈问题
            interview_question = self._generate_interview_question(turn_idx, turns)

            # 检查是否主动结束
            if interview_question.strip().startswith(_DONE_MARKER):
                logger.info("[Interviewer] LLM decided to end interview at turn %d.", turn_idx)
                break

            print(f"\n{'='*60}")
            print(f"[Interviewer - Round {turn_idx}/{turns}]")

            # 追加到 memory（作为 assistant 的发言）
            self.add_to_memory("assistant", interview_question)

            # 让 HumanCustomer 回答
            customer_reply = human_customer.answer(question=interview_question)

            # 将 customer 回复追加到 interviewer memory（role=user）
            self.add_to_memory("user", f"[Customer Reply]\n{customer_reply}")

            # 保存到对话历史
            dialogue_history.append({"role": "interviewer", "content": interview_question})
            dialogue_history.append({"role": "customer", "content": customer_reply})

            # 每 3 轮做一次 memory 压缩（保留 system + 问题清单 + 最近 6 条）
            if turn_idx % 3 == 0:
                self._compress_memory(question_list, project_description)

        # ── Step 3: 保存完整对话记录 ──────────────────────────────────────
        dialogue_md = self._format_dialogue_md(project_description, dialogue_history)
        dialogue_path = os.path.join(output_dir, "customer_dialogue.md")
        self._write_file(dialogue_path, dialogue_md)
        logger.info("[Interviewer] Dialogue saved to %s", dialogue_path)
        print(f"\n[Interviewer] 访谈结束，对话已保存至：{dialogue_path}")

        return dialogue_path

    def _generate_question_list(self, project_description: str) -> str:
        """
        Step 1a: 理解 BRD 模版 → 生成访谈问题清单。
        使用独立 messages，不污染主 memory。
        """
        kb_prompt = self.get_knowledge_prompt("interview_with_customer")
        messages = [
            self.llm.format_message("system", self.system_prompt),
            self.llm.format_message(
                "user",
                f"""You are preparing for a requirements interview.

{kb_prompt}

## Project Description (provided by customer)
{project_description}

## BRD Template (this is what you need to produce)
{self.brd_template}

## Task
Based on the BRD template sections and the project description, generate a comprehensive numbered list of questions you need to ask the customer during the interview.

Guidelines:
- Each question should target a specific BRD section or field.
- Questions should be in plain business language.
- Order them logically (context → objectives → scope → requirements → constraints → risks).
- Include 1–2 probing sub-questions when a topic is likely to need clarification.

Output ONLY the numbered question list in Markdown format.{self.lang_reminder}""",
            ),
        ]
        question_list = self.llm.generate(
            messages=messages,
            temperature=self.llm_params.get("temperature", 0.3),
            max_output_tokens=self.llm_params.get("max_output_tokens", 2048),
        )
        result = (question_list or "").strip()
        if not result:
            logger.warning("[Interviewer] LLM returned empty question list, using fallback.")
            result = (
                "1. What is the main purpose and goal of this system?\n"
                "2. Who are the primary users and stakeholders?\n"
                "3. What are the key features and functionalities required?\n"
                "4. Are there any specific technical constraints or requirements?\n"
                "5. What is the expected timeline and budget?\n"
                "6. Are there any existing systems this needs to integrate with?\n"
                "7. What are the primary success criteria?"
            )
        return result

    def _generate_interview_question(self, turn_idx: int, max_turns: int) -> str:
        """
        Step 2: 基于当前 memory 和对话历史，生成下一个访谈问题。
        若已收集足够信息且超过最小轮次，返回 [DONE]... 信号。
        """
        # 最少进行 5 轮访谈，确保收集到足够信息
        min_rounds = 5
        remaining = max_turns - turn_idx

        if turn_idx < min_rounds:
            # 未达到最小轮次，不允许 [DONE]
            guidance_prompt = (
                f"You are on interview round {turn_idx} of at most {max_turns} total rounds "
                f"({remaining} rounds remaining after this).\n\n"
                "Based on your question list and the conversation so far:\n"
                "- Ask the SINGLE most important unanswered question from your list.\n"
                "- You MUST continue asking questions — do NOT stop the interview yet.\n"
                "- Focus on areas that have NOT been covered yet.\n"
                "Output ONLY the question. No preamble."
                f"{self.lang_reminder}"
            )
        else:
            guidance_prompt = (
                f"You are on interview round {turn_idx} of at most {max_turns} total rounds "
                f"({remaining} rounds remaining after this).\n\n"
                "Based on your question list and the conversation so far:\n"
                "- If you have gathered sufficient information to write a full BRD, respond with exactly: "
                f"`{_DONE_MARKER} Sufficient information collected.`\n"
                "- Otherwise, ask the SINGLE most important unanswered question from your list.\n"
                "Output ONLY the question (or the DONE marker). No preamble."
                f"{self.lang_reminder}"
            )

        # 临时追加指导，不持久化到 memory
        messages = self._memory + [self.llm.format_message("user", guidance_prompt)]
        response = self.llm.generate(
            messages=messages,
            temperature=self.llm_params.get("temperature", 0.2),
            max_output_tokens=512,
        )
        result = (response or "").strip()
        if not result:
            logger.warning("[Interviewer] LLM returned empty question at turn %d, using fallback.", turn_idx)
            result = "Could you please elaborate more on the key features and functionality you envision for this system?"
        return result

    def _compress_memory(self, question_list: str, project_description: str) -> None:
        """
        每 N 轮对话后，压缩 memory：
        保留 system_prompt + 问题清单 + 最近 6 条对话（3 轮 Q&A）。
        """
        recent = self._memory[-6:] if len(self._memory) >= 6 else self._memory[:]

        compressed_head = [
            self.llm.format_message("system", self.system_prompt),
            self.llm.format_message(
                "assistant",
                f"My question list:\n{question_list}"
            ),
            self.llm.format_message(
                "user",
                f"Project description: {project_description[:300]}…\n[Earlier dialogue compressed]"
            ),
        ]
        self._memory = compressed_head + recent
        logger.debug("[Interviewer] Memory compressed to %d messages.", len(self._memory))

    @staticmethod
    def _format_dialogue_md(project_description: str, history: List[Dict[str, str]]) -> str:
        """将对话历史格式化为 Markdown 文档。"""
        lines = [
            "# Customer Interview Dialogue",
            "",
            f"> **Recorded**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            "---",
            "",
            "## Project Description",
            "",
            project_description,
            "",
            "---",
            "",
            "## Interview Transcript",
            "",
        ]
        for entry in history:
            role = entry["role"]
            content = entry["content"]
            if role == "customer_description":
                continue  # 已在前面单独输出
            if role == "interviewer":
                lines.append(f"**[Interviewer]** {content}")
            elif role == "customer":
                lines.append(f"**[Customer]** {content}")
            lines.append("")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Action 2: generate_BRD
    # ------------------------------------------------------------------

    def generate_BRD(
        self,
        dialogue_content: str,
        output_dir: str,
    ) -> str:
        """
        基于完整对话历史，按 BRD 模版分 Section 逐段撰写 BRD，保存到文件。

        Parameters
        ----------
        dialogue_content:
            customer_dialogue.md 的完整文本内容
        output_dir:
            制品输出目录

        Returns
        -------
        str
            BRD 文件路径
        """
        logger.info("[Interviewer] Starting BRD generation...")

        # ── 刷新 memory：回到 system_prompt 干净状态 ─────────────────────
        self.refresh_memory([{"role": "system", "content": self.system_prompt}])

        # 将 知识库 + BRD 模版 + 对话记录加入 memory
        kb_prompt = self.get_knowledge_prompt("generate_BRD")
        self.add_to_memory(
            "user",
            f"{kb_prompt}\n\n## BRD Template\n{self.brd_template}\n\n## Interview Dialogue\n{dialogue_content}",
        )
        self.add_to_memory(
            "assistant",
            (
                "I have reviewed the BRD template and the full interview dialogue. "
                "I will now write the BRD section by section."
            ),
        )

        # ── 解析 BRD 模版中的 Section 标题 ───────────────────────────────
        sections = self._parse_brd_sections(self.brd_template)
        logger.info("[Interviewer] BRD sections to write: %s", [s[0] for s in sections])

        written_sections: List[str] = []

        # ── 逐 Section 生成 ───────────────────────────────────────────────
        for section_header, section_template in sections:
            logger.info("[Interviewer] Writing: %s", section_header)

            prompt = (
                f"Write the following BRD section based on the interview dialogue above.\n\n"
                f"**Section to write**: {section_header}\n\n"
                f"**Template structure for this section**:\n{section_template}\n\n"
                "Guidelines:\n"
                "- Fill in ALL placeholders based on information from the dialogue.\n"
                "- If a piece of information was not discussed, mark it as `[TBD]`.\n"
                "- Keep language business-focused, clear, and professional.\n"
                "- Output ONLY the Markdown content for this section (starting from the ## heading)."
                f"{self.lang_reminder}"
            )

            self.add_to_memory("user", prompt)
            section_content = self.generate_response()
            self.add_to_memory("assistant", section_content)

            written_sections.append(section_content.strip())

            # memory 积累过多时做一次压缩（保留 head + 最近 4 条）
            if len(self._memory) > 14:
                self._compress_brd_memory(dialogue_content)

        # ── 组合完整 BRD ──────────────────────────────────────────────────
        brd_header = (
            f"# Business Requirements Document\n\n"
            f"> **Project**: (see Section 1)\n"
            f"> **Generated by**: iReDev InterviewerAgent\n"
            f"> **Date**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n---\n\n"
        )
        full_brd = brd_header + "\n\n---\n\n".join(written_sections)

        brd_path = os.path.join(output_dir, "BRD.md")
        self._write_file(brd_path, full_brd)
        logger.info("[Interviewer] BRD saved to %s", brd_path)
        print(f"\n[Interviewer] BRD 已生成并保存至：{brd_path}")

        return brd_path

    @staticmethod
    def _parse_brd_sections(template: str) -> List[tuple[str, str]]:
        """
        从 BRD 模版中提取各 Section（以 '## Section N' 开头的块）。

        Returns
        -------
        List[Tuple[section_header, section_body]]
        """
        sections: List[tuple[str, str]] = []
        # 以 ## Section 为分隔符拆分
        pattern = re.compile(r"(## Section \d+[^\n]*)", re.IGNORECASE)
        parts = pattern.split(template)

        # parts 格式: [pre, header1, body1, header2, body2, ...]
        i = 1
        while i < len(parts) - 1:
            header = parts[i].strip()
            body = parts[i + 1].strip()
            sections.append((header, body))
            i += 2

        if not sections:
            # 降级：把整个模版作为一个 section
            sections = [("## Business Requirements Document", template)]

        return sections

    def _compress_brd_memory(self, dialogue_content: str) -> None:
        """BRD 生成期间的 memory 压缩：保留 system_prompt + dialogue + 最近 4 条。"""
        recent = self._memory[-4:] if len(self._memory) >= 4 else self._memory[:]
        self._memory = [
            self.llm.format_message("system", self.system_prompt),
            self.llm.format_message("user", f"[Interview Dialogue]\n{dialogue_content[:2000]}…"),
            self.llm.format_message("assistant", "Continuing BRD generation..."),
        ] + recent
        logger.debug("[Interviewer] BRD memory compressed to %d messages.", len(self._memory))

    # ------------------------------------------------------------------
    # Action 3: capture_persona
    # ------------------------------------------------------------------

    def capture_persona(
        self,
        brd_content: str,
        output_dir: str,
    ) -> str:
        """
        读取 BRD.md → 生成 UserList.md + PlantUML 上下文图 + 渲染 PNG。

        Parameters
        ----------
        brd_content:
            BRD.md 的完整文本
        output_dir:
            制品输出目录

        Returns
        -------
        str
            UserList.md 文件路径
        """
        logger.info("[Interviewer] capture_persona: analyzing BRD for end-user personas...")

        # ── Step 1: 生成用户角色列表 ──────────────────────────────────────
        personas, userlist_md = self._generate_user_list(brd_content)
        userlist_path = os.path.join(output_dir, "UserList.md")
        self._write_file(userlist_path, userlist_md)
        logger.info("[Interviewer] UserList.md saved (%d personas).", len(personas))
        print(f"\n[Interviewer] 识别到 {len(personas)} 个用户角色，已保存 UserList.md")

        # ── Step 2: 生成 PlantUML 上下文图并渲染 ─────────────────────────
        try:
            puml_text = self._generate_context_diagram(personas, brd_content)
            puml_path = os.path.join(output_dir, "context_diagram.puml")
            self._write_file(puml_path, puml_text)
            png_path = os.path.join(output_dir, "context_diagram.png")
            self._render_plantuml(puml_text, png_path)
            logger.info("[Interviewer] Context diagram saved: %s", png_path)
            print(f"[Interviewer] 上下文图已渲染保存：{png_path}")
        except Exception as exc:
            logger.warning("[Interviewer] Context diagram rendering failed: %s", exc)
            print(f"[Interviewer] ⚠ 上下文图渲染失败（{exc}），跳过。")

        return userlist_path

    def _generate_user_list(
        self, brd_content: str
    ) -> Tuple[List[Dict[str, str]], str]:
        """
        调用 LLM 分析 BRD，识别终端用户角色，按 UserList 模版格式化。

        Returns
        -------
        personas:
            [{"name": ..., "description": ...}, ...]
        userlist_md:
            完整 UserList.md Markdown 文本
        """
        kb_prompt = self.get_knowledge_prompt("capture_persona")
        messages = [
            self.llm.format_message("system", self.system_prompt),
            self.llm.format_message(
                "user",
                f"""Analyze the following Business Requirements Document and identify all distinct end-user roles (personas) that will interact with the system.

{kb_prompt}

## BRD Content
{brd_content}

## UserList Template
{self.userlist_template}

## Task
Output a JSON array of user personas, then the complete UserList.md in Markdown.

Format your response EXACTLY as follows (no other text):

```json
[{{"name": "Role Name", "description": "Brief role description"}}]
```

```markdown
<complete UserList.md content following the template, with {{USER_ROWS}} replaced by actual table rows>
```{self.lang_reminder}"""
            ),
        ]

        raw = self.llm.generate(
            messages=messages,
            temperature=0.2,
            max_output_tokens=self.llm_params.get("max_output_tokens", 2048),
        )

        # 解析 JSON 部分
        personas: List[Dict[str, str]] = []
        json_match = re.search(r"```json\s*(\[.*?\])\s*```", raw, re.DOTALL)
        if json_match:
            try:
                personas = json.loads(json_match.group(1))
            except json.JSONDecodeError:
                logger.warning("[Interviewer] Failed to parse persona JSON.")

        # 解析 Markdown 部分
        md_match = re.search(r"```markdown\s*(.*?)\s*```", raw, re.DOTALL)
        if md_match:
            userlist_md = md_match.group(1)
        else:
            # Fallback: 直接用 raw（LLM 可能没加 fence）
            userlist_md = raw.strip()

        # 若 JSON 解析失败，从 Markdown 表格回退解析
        if not personas:
            personas = self._parse_personas_from_markdown(userlist_md)

        return personas, userlist_md

    @staticmethod
    def _parse_personas_from_markdown(md: str) -> List[Dict[str, str]]:
        """Fallback: 从 Markdown 表格行中提取角色信息。"""
        personas = []
        for line in md.splitlines():
            # 匹配 | No. | Role | Description | 格式的数据行
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) >= 3 and cells[0].isdigit():
                personas.append({"name": cells[1], "description": cells[2]})
        return personas

    def _generate_context_diagram(self, personas: List[Dict[str, str]], brd_content: str) -> str:
        """
        调用 LLM 生成 PlantUML 上下文图（Context Diagram），
        展示各 enduser 角色与核心系统的交互关系。
        """
        persona_list = "\n".join(
            f'- {p["name"]}: {p["description"]}' for p in personas
        )
        messages = [
            self.llm.format_message("system", self.system_prompt),
            self.llm.format_message(
                "user",
                f"""Generate a PlantUML Context Diagram showing all end-user roles and their interactions with the system.

## User Roles
{persona_list}

## System Context (from BRD)
{brd_content[:1500]}

## Requirements
- Use PlantUML `@startuml` / `@enduml`
- Use `actor` for each user role
- Use a central `rectangle` or `component` for the system
- Use arrows with brief labels to indicate the type of interaction
- Keep it clean and readable

Output ONLY valid PlantUML code (no explanation, no markdown fences).{self.lang_reminder}"""
            ),
        ]
        puml = self.llm.generate(
            messages=messages,
            temperature=0.2,
            max_output_tokens=1024,
        )
        # 清理可能包裹的 fence
        puml = puml.strip()
        if puml.startswith("```"):
            puml = re.sub(r"^```[a-z]*\n?", "", puml)
            puml = re.sub(r"\n?```$", "", puml).strip()
        return puml

    def _render_plantuml(self, puml_text: str, output_path: str) -> None:
        """
        调用 PlantUML 公共 API 将 PlantUML 文本渲染为 PNG 并保存。

        Raises
        ------
        RuntimeError
            若 HTTP 请求失败。
        """
        encoded = _plantuml_encode(puml_text)
        url = _PLANTUML_API + encoded
        logger.debug("[Interviewer] Requesting PlantUML: %s", url[:80])

        req = urllib.request.Request(
            url,
            headers={"User-Agent": "iReDev-RequirementsBot/1.0"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            if resp.status != 200:
                raise RuntimeError(f"PlantUML API returned HTTP {resp.status}")
            png_data = resp.read()

        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "wb") as f:
            f.write(png_data)

    # ------------------------------------------------------------------
    # Action 4: interview_with_enduser
    # ------------------------------------------------------------------

    def interview_with_enduser(
        self,
        userlist_content: str,
        output_dir: str,
        config_path: Optional[str] = None,
        max_turns_per_enduser: Optional[int] = None,
        brd_content: Optional[str] = None,
    ) -> str:
        """
        解析 UserList.md → 对每类 EndUser 实例化并逐一访谈 → 保存 enduser_dialogue.md。

        Parameters
        ----------
        userlist_content:
            UserList.md 的完整文本
        output_dir:
            制品输出目录
        config_path:
            LLM 配置文件路径（传给 EndUserAgent）
        max_turns_per_enduser:
            每个角色的最大访谈轮数，None 时使用 self.max_turns_per_enduser
        brd_content:
            BRD.md 的完整文本，用于为 EndUserAgent 提供项目背景信息
        """
        max_turns = max_turns_per_enduser or self.max_turns_per_enduser

        # ── Step 0: 准备项目上下文（从 BRD 中提取摘要） ─────────────
        project_context = ""
        if brd_content:
            # 截取 BRD 的前 2000 字符作为项目上下文，避免 prompt 过长
            project_context = brd_content[:2000]
        else:
            # 尝试从磁盘读取 BRD.md
            brd_path = os.path.join(output_dir, "BRD.md")
            if os.path.isfile(brd_path):
                with open(brd_path, "r", encoding="utf-8") as f:
                    project_context = f.read()[:2000]
        if not project_context:
            # 回退：尝试读取项目描述
            desc_path = os.path.join(output_dir, "customer_project_description.md")
            if os.path.isfile(desc_path):
                with open(desc_path, "r", encoding="utf-8") as f:
                    project_context = f.read()[:2000]

        # ── Step 1: 解析用户角色列表 ──────────────────────────────────────
        personas = self._parse_personas_from_markdown(userlist_content)
        if not personas:
            logger.warning("[Interviewer] No personas found in UserList.md, aborting.")
            return ""
        logger.info("[Interviewer] Interviewing %d personas.", len(personas))

        # ── Step 2: 实例化 EndUserAgent 列表 ─────────────────────────────
        agents: List[EndUserAgent] = [
            EndUserAgent(
                persona_name=p["name"],
                persona_description=p["description"],
                config_path=config_path or self.llm_params.get("config_path"),
                language=self.language,
                project_context=project_context,
            )
            for p in personas
        ]

        # ── Step 3: 依次访谈每个 EndUserAgent ────────────────────────────
        all_dialogues: List[Dict[str, Any]] = []
        for enduser_agent in agents:
            qa_pairs = self._run_single_enduser_interview(enduser_agent, max_turns)
            all_dialogues.append({
                "persona_name": enduser_agent.persona_name,
                "persona_description": enduser_agent.persona_description,
                "qa": qa_pairs,
            })
            print(f"\n[Interviewer] ✓ {enduser_agent.persona_name} 访谈完成（{len(qa_pairs)} 轮）")

        # ── Step 4: 拼接整理 → 保存 enduser_dialogue.md ──────────────────
        dialogue_md = self._format_enduser_dialogue_md(all_dialogues)
        dialogue_path = os.path.join(output_dir, "enduser_dialogue.md")
        self._write_file(dialogue_path, dialogue_md)
        logger.info("[Interviewer] enduser_dialogue.md saved to %s", dialogue_path)
        print(f"\n[Interviewer] 所有终端用户访谈完成，对话记录保存至：{dialogue_path}")

        return dialogue_path

    def _run_single_enduser_interview(
        self,
        enduser_agent: EndUserAgent,
        max_turns: int,
    ) -> List[Dict[str, str]]:
        """
        对单个 EndUserAgent 进行多轮访谈，返回 QA 对列表。
        Interviewer 用带角色信息的 prompt 生成问题；EndUser 用 LLM 生成回答。
        """
        persona_name = enduser_agent.persona_name
        persona_desc = enduser_agent.persona_description
        logger.info("[Interviewer] Starting interview with: %s", persona_name)
        print(f"\n{'='*60}")
        print(f"[Interviewer] 开始访谈角色：{persona_name}")
        print(f"[描述] {persona_desc}")

        # 个性化 enduser 访谈 system prompt（用于生成问题的 interviewer 侧）
        project_ctx = getattr(enduser_agent, "project_context", "") or ""
        kb_prompt = self.get_knowledge_prompt("interview_with_enduser")
        enduser_interview_system = (
            self.enduser_q_prompt
            .replace("{PERSONA_NAME}", persona_name)
            .replace("{PERSONA_DESCRIPTION}", persona_desc)
            .replace("{PROJECT_CONTEXT}", project_ctx or "No project context provided.")
        )
        if kb_prompt:
            enduser_interview_system += "\n\n" + kb_prompt

        # 独立的 interviewer memory（不污染主 memory）
        iv_memory: List[Dict[str, Any]] = [
            self.llm.format_message("system", enduser_interview_system),
            self.llm.format_message(
                "user",
                f"Please start the interview with {persona_name}."
            ),
        ]

        qa_pairs: List[Dict[str, str]] = []

        for turn_idx in range(1, max_turns + 1):
            # ── Interviewer 生成问题 ──────────────────────────────────────
            remaining = max_turns - turn_idx
            guidance = (
                f"Round {turn_idx}/{max_turns} ({remaining} remaining). "
                "Ask the most important unanswered question, or output "
                f"`{_DONE_MARKER}` if enough info is collected."
                f"{self.lang_reminder}"
            )
            iv_memory.append(self.llm.format_message("user", guidance))

            question_raw = self.llm.generate(
                messages=iv_memory,
                temperature=0.3,
                max_output_tokens=300,
            )
            question = question_raw.strip()

            # 移除临时 guidance 消息（不保留在 memory 中）
            iv_memory.pop()

            if question.startswith(_DONE_MARKER):
                logger.info("[Interviewer] Interview ended early for %s at turn %d.", persona_name, turn_idx)
                break

            print(f"\n[Interviewer→{persona_name} | Round {turn_idx}] {question}")

            # ── EndUser 生成回答 ──────────────────────────────────────────
            answer = enduser_agent.answer(question)
            print(f"[{persona_name}] {answer}")

            # 追加到 interviewer 侧 memory
            iv_memory.append(self.llm.format_message("assistant", question))
            iv_memory.append(self.llm.format_message("user", f"[{persona_name}] {answer}"))

            qa_pairs.append({"question": question, "answer": answer})

            # memory 超长时压缩（保留 head + 最近 6 条）
            if len(iv_memory) > 14:
                iv_memory = iv_memory[:2] + iv_memory[-6:]

        return qa_pairs

    @staticmethod
    def _format_enduser_dialogue_md(all_dialogues: List[Dict[str, Any]]) -> str:
        """将所有角色的 QA 对列表格式化为 enduser_dialogue.md。"""
        lines = [
            "# End-User Interview Dialogue",
            "",
            f"> **Recorded**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            "---",
            "",
        ]
        for dialogue in all_dialogues:
            pname = dialogue["persona_name"]
            pdesc = dialogue["persona_description"]
            qa = dialogue["qa"]
            lines += [
                f"## Persona: {pname}",
                "",
                f"**Description**: {pdesc}",
                "",
                f"**Turns**: {len(qa)}",
                "",
            ]
            for i, pair in enumerate(qa, 1):
                lines.append(f"**[Round {i}]**")
                lines.append(f"**Interviewer**: {pair['question']}")
                lines.append("")
                lines.append(f"**{pname}**: {pair['answer']}")
                lines.append("")
            lines.append("---")
            lines.append("")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Action 5: write_userRD
    # ------------------------------------------------------------------

    def write_userRD(
        self,
        enduser_dialogue_content: str,
        output_dir: str,
    ) -> str:
        """
        根据 enduser_dialogue.md + UserRD 模版，分章节撰写用户需求文档 UserRD.md。

        Parameters
        ----------
        enduser_dialogue_content:
            enduser_dialogue.md 的完整文本内容
        output_dir:
            制品输出目录

        Returns
        -------
        str
            UserRD.md 文件路径
        """
        logger.info("[Interviewer] Starting UserRD generation...")

        # ── Memory 重置 ────────────────────────────────────────────────
        self.refresh_memory([{"role": "system", "content": self.system_prompt}])
        kb_prompt = self.get_knowledge_prompt("write_userRD")
        self.add_to_memory(
            "user",
            f"{kb_prompt}\n\n## UserRD Template\n{self.userrd_template}\n\n"
            f"## End-User Interview Dialogue\n{enduser_dialogue_content}",
        )
        self.add_to_memory(
            "assistant",
            "I have reviewed the UserRD template and all end-user interview dialogues. "
            "I will now write the User Requirements Document section by section.",
        )

        # ── 解析模版章节 ────────────────────────────────────────────────
        sections = self._parse_userrd_sections(self.userrd_template)
        logger.info("[Interviewer] UserRD sections: %s", [s[0] for s in sections])

        written_sections: List[str] = []

        for section_header, section_template in sections:
            logger.info("[Interviewer] Writing UserRD section: %s", section_header)
            prompt = (
                f"Write the following section of the User Requirements Document "
                f"based on the interview dialogues above.\n\n"
                f"**Section**: {section_header}\n\n"
                f"**Template structure**:\n{section_template}\n\n"
                "Guidelines:\n"
                "- Fill all placeholders with information from the interview dialogues.\n"
                "- For requirements sections, create one requirement block per identified need.\n"
                "- Use user story format: As a [role], I want [feature], so that [benefit].\n"
                "- Mark any missing information as `[TBD]`.\n"
                "- Output ONLY the Markdown content for this section."
                f"{self.lang_reminder}"
            )
            self.add_to_memory("user", prompt)
            section_content = self.generate_response()
            self.add_to_memory("assistant", section_content)
            written_sections.append(section_content.strip())

            if len(self._memory) > 14:
                self._compress_userrd_memory(enduser_dialogue_content)

        # ── 拼合完整 UserRD.md ─────────────────────────────────────────
        header = (
            f"# User Requirements Document (UserRD)\n\n"
            f"> **Generated by**: iReDev InterviewerAgent\n"
            f"> **Date**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n---\n\n"
        )
        full_userrd = header + "\n\n---\n\n".join(written_sections)

        userrd_path = os.path.join(output_dir, "UserRD.md")
        self._write_file(userrd_path, full_userrd)
        logger.info("[Interviewer] UserRD.md saved to %s", userrd_path)
        print(f"\n[Interviewer] UserRD.md 已生成：{userrd_path}")

        return userrd_path

    @staticmethod
    def _parse_userrd_sections(template: str) -> List[Tuple[str, str]]:
        """
        从 UserRD 模版中提取各章节（以 `# N.` 或 `# ` 开头的一级标题块）。
        """
        sections: List[Tuple[str, str]] = []
        # 以一级标题（# 开头）为分隔符分割
        pattern = re.compile(r"(^# .+$)", re.MULTILINE)
        parts = pattern.split(template)

        i = 1
        while i < len(parts) - 1:
            header = parts[i].strip()
            body = parts[i + 1].strip()
            sections.append((header, body))
            i += 2

        if not sections:
            sections = [("# User Requirements Document", template)]

        return sections

    def _compress_userrd_memory(self, dialogue_content: str) -> None:
        """UserRD 生成期间的 memory 压缩：保留 system_prompt + dialogue 摘要 + 最近 4 条。"""
        recent = self._memory[-4:] if len(self._memory) >= 4 else self._memory[:]
        self._memory = [
            self.llm.format_message("system", self.system_prompt),
            self.llm.format_message(
                "user",
                f"[End-User Interview Dialogue - truncated]\n{dialogue_content[:2000]}…"
            ),
            self.llm.format_message("assistant", "Continuing UserRD generation..."),
        ] + recent

    # ------------------------------------------------------------------
    # BaseAgent 抽象方法
    # ------------------------------------------------------------------

    def process(self, action: str, **kwargs) -> Any:
        """
        分发执行 interviewer 的动作。

        支持的 action：
          - "interview_with_customer"
          - "generate_BRD"
          - "capture_persona"
          - "interview_with_enduser"
          - "write_userRD"
        """
        logger.info("[%s] >>> Executing action: %s", self.agent_name, action)
        if action == "interview_with_customer":
            return self.interview_with_customer(
                project_description=kwargs["project_description"],
                human_customer=kwargs["human_customer"],
                output_dir=kwargs["output_dir"],
                max_turns=kwargs.get("max_turns"),
            )
        elif action == "generate_BRD":
            return self.generate_BRD(
                dialogue_content=kwargs["dialogue_content"],
                output_dir=kwargs["output_dir"],
            )
        elif action == "capture_persona":
            return self.capture_persona(
                brd_content=kwargs["brd_content"],
                output_dir=kwargs["output_dir"],
            )
        elif action == "interview_with_enduser":
            return self.interview_with_enduser(
                userlist_content=kwargs["userlist_content"],
                output_dir=kwargs["output_dir"],
                config_path=kwargs.get("config_path"),
                max_turns_per_enduser=kwargs.get("max_turns_per_enduser"),
                brd_content=kwargs.get("brd_content"),
            )
        elif action == "write_userRD":
            return self.write_userRD(
                enduser_dialogue_content=kwargs["enduser_dialogue_content"],
                output_dir=kwargs["output_dir"],
            )
        else:
            raise ValueError(f"InterviewerAgent: unknown action '{action}'")

