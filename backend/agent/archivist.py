"""
ArchivistAgent — 需求文档归档与维护代理

职责（由制品池事件 / 编排器直接调用）：
  1. write_SRS  — SyRS.md 产出后触发
       从 BRD / UserRD / SyRS 中按需检索，按 SRS 模版分章节撰写最终 SRS.md
  2. revise_SRS — 审查报告 issue_X.md 产出后触发
       读取审查报告，对 SRS 执行最小化定点修改
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from .base import BaseAgent

logger = logging.getLogger(__name__)

_SRS_TEMPLATE_PATH  = "backend/knowledge/SRS_template.md"
_SRS_TEMPLATE_PATH_ZH = "backend/knowledge/SRS_template_zh.md"
_DEFAULT_PROMPT_PATH = "backend/prompt/archivist/system_prompt.txt"


class ArchivistAgent(BaseAgent):
    """
    Requirements Archivist Agent.

    对外接口（通过 process）：
      - process("write_SRS", brd_content=..., userrd_content=..., syrs_content=..., output_dir=...)
      - process("revise_SRS", srs_content=..., issue_content=..., brd_content=...,
                 userrd_content=..., syrs_content=..., output_dir=...)
    """

    def __init__(
        self,
        name: str = "Archivist",
        prompt_path: str = _DEFAULT_PROMPT_PATH,
        config_path: Optional[str] = None,
        srs_template_path: str = _SRS_TEMPLATE_PATH,
        language: str = "en",
    ):
        super().__init__(name=name, prompt_path=prompt_path, config_path=config_path, language=language)
        # Select template path based on language
        if language == "zh":
            self.srs_template_path = _SRS_TEMPLATE_PATH_ZH
        else:
            self.srs_template_path = srs_template_path
        self._srs_template: Optional[str] = None

    # ------------------------------------------------------------------
    # 属性 / 工具
    # ------------------------------------------------------------------

    @property
    def srs_template(self) -> str:
        if self._srs_template is None:
            with open(self.srs_template_path, "r", encoding="utf-8") as f:
                self._srs_template = f.read()
        return self._srs_template

    @staticmethod
    def _write_file(path: str, content: str) -> None:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

    def _call_llm_fresh(self, user_prompt: str, max_tokens: Optional[int] = None) -> str:
        """独立单次 LLM 调用（不影响 memory）。"""
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
    # 模版解析
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_srs_sections(template: str) -> List[Tuple[str, str]]:
        """
        按一级标题（``# N.`` 或 ``# Appendix``）拆分 SRS 模版章节。
        返回 [(heading, body), ...]。
        """
        sections: List[Tuple[str, str]] = []
        pattern = re.compile(r"(^# .+$)", re.MULTILINE)
        parts = pattern.split(template)

        i = 1
        while i < len(parts) - 1:
            header = parts[i].strip()
            body = parts[i + 1].strip()
            sections.append((header, body))
            i += 2

        if not sections:
            sections = [("# Software Requirements Specification", template)]
        return sections

    # ------------------------------------------------------------------
    # 上下文检索辅助
    # ------------------------------------------------------------------

    @staticmethod
    def _retrieve_relevant_context(
        section_header: str,
        brd: str,
        userrd: str,
        syrs: str,
        max_chars: int = 3000,
    ) -> str:
        """
        根据章节标题启发式选取最相关的上游文档片段，控制总长度。

        检索策略：
          - Introduction / Scope / References → BRD 优先
          - User Classes / Features → UserRD + SyRS
          - Quality / Performance / Security → SyRS 优先
          - External Interface → SyRS
          - Data / Appendix → UserRD + SyRS
          - 其他 → 均匀截取
        """
        header_lower = section_header.lower()

        snippets: List[str] = []

        # ---- 权重决策 ----
        if any(kw in header_lower for kw in ("introduction", "scope", "purpose", "reference", "convention")):
            snippets.append(f"### BRD (excerpt)\n{brd[:max_chars]}")
            snippets.append(f"### SyRS (excerpt)\n{syrs[:max_chars // 2]}")

        elif any(kw in header_lower for kw in ("user class", "user", "feature", "system feature")):
            snippets.append(f"### UserRD (excerpt)\n{userrd[:max_chars]}")
            snippets.append(f"### SyRS (excerpt)\n{syrs[:max_chars]}")

        elif any(kw in header_lower for kw in ("quality", "performance", "security", "safety", "usability")):
            snippets.append(f"### SyRS (excerpt)\n{syrs[:max_chars]}")
            snippets.append(f"### UserRD (excerpt)\n{userrd[:max_chars // 3]}")

        elif any(kw in header_lower for kw in ("interface", "external")):
            snippets.append(f"### SyRS (excerpt)\n{syrs[:max_chars]}")

        elif any(kw in header_lower for kw in ("data", "appendix", "glossary", "model")):
            snippets.append(f"### UserRD (excerpt)\n{userrd[:max_chars // 2]}")
            snippets.append(f"### SyRS (excerpt)\n{syrs[:max_chars // 2]}")

        elif any(kw in header_lower for kw in ("overall", "description", "product", "environment", "constraint",
                                                 "assumption", "operating")):
            snippets.append(f"### BRD (excerpt)\n{brd[:max_chars // 2]}")
            snippets.append(f"### SyRS (excerpt)\n{syrs[:max_chars // 2]}")
            snippets.append(f"### UserRD (excerpt)\n{userrd[:max_chars // 3]}")

        elif any(kw in header_lower for kw in ("internationalization", "localization", "other")):
            snippets.append(f"### SyRS (excerpt)\n{syrs[:max_chars // 2]}")
            snippets.append(f"### BRD (excerpt)\n{brd[:max_chars // 3]}")

        else:
            third = max_chars // 3
            snippets.append(f"### BRD (excerpt)\n{brd[:third]}")
            snippets.append(f"### UserRD (excerpt)\n{userrd[:third]}")
            snippets.append(f"### SyRS (excerpt)\n{syrs[:third]}")

        return "\n\n".join(snippets)

    # ------------------------------------------------------------------
    # Action 1: write_SRS
    # ------------------------------------------------------------------

    def write_SRS(
        self,
        brd_content: str,
        userrd_content: str,
        syrs_content: str,
        output_dir: str,
    ) -> str:
        """
        按 SRS 模版分章节撰写，最后拼接为完整 SRS.md。

        Returns
        -------
        str  — SRS.md 文件路径
        """
        logger.info("[Archivist] write_SRS: composing SRS section by section...")

        # 初始化 memory
        self.refresh_memory([{"role": "system", "content": self.system_prompt}])
        kb_prompt = self.get_knowledge_prompt("write_SRS")
        self.add_to_memory(
            "user",
            f"{kb_prompt}\n\n"
            "I will ask you to write each section of the SRS one at a time. "
            "For each section I will provide the template guidance and relevant excerpts from "
            "BRD, UserRD, and SyRS. Respond with ONLY the Markdown for that section.",
        )
        self.add_to_memory("assistant", "Understood. Please provide the first section.")

        sections = self._parse_srs_sections(self.srs_template)
        logger.info("[Archivist] SRS sections to write: %s", [s[0] for s in sections])

        written: List[str] = []

        for section_header, section_body in sections:
            logger.info("[Archivist] Writing: %s", section_header)

            context = self._retrieve_relevant_context(
                section_header, brd_content, userrd_content, syrs_content,
            )

            prompt = (
                f"Write the following SRS section.\n\n"
                f"**Section**: {section_header}\n\n"
                f"**Template guidance**:\n{section_body}\n\n"
                f"**Relevant upstream artifacts**:\n{context}\n\n"
                "Instructions:\n"
                "- Replace ALL template placeholders with substantive content derived from the artifacts.\n"
                "- For 'System Features' (section 3), create one subsection (3.X) per major feature group, "
                "each containing a description and a functional requirements table with IDs.\n"
                "- Maintain traceability — cite source document sections.\n"
                "- Mark genuinely unknown items as `[TBD]`.\n"
                "- Output ONLY the Markdown for this section (starting with the heading)."
                f"{self.lang_reminder}"
            )

            self.add_to_memory("user", prompt)
            section_md = self.generate_response()
            self.add_to_memory("assistant", section_md)
            written.append(section_md.strip())

            # 防止 memory 溢出
            if len(self._memory) > 16:
                self._compress_memory()

        # ── 拼接完整 SRS ─────────────────────────────────────────────────
        header = (
            "# Software Requirements Specification (SRS)\n\n"
            "> **Generated by**: iReDev ArchivistAgent\n"
            f"> **Date**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            "> **Status**: Draft — Pending Review\n\n---\n\n"
        )
        full_srs = header + "\n\n---\n\n".join(written)

        srs_path = os.path.join(output_dir, "SRS.md")
        self._write_file(srs_path, full_srs)
        logger.info("[Archivist] SRS.md saved to %s", srs_path)
        print(f"\n[Archivist] ✓ SRS.md 已生成：{srs_path}")
        return srs_path

    # ------------------------------------------------------------------
    # Action 2: revise_SRS
    # ------------------------------------------------------------------

    def revise_SRS(
        self,
        srs_content: str,
        issue_content: str,
        brd_content: str,
        userrd_content: str,
        syrs_content: str,
        output_dir: str,
    ) -> str:
        """
        根据审查报告 (issue) 对 SRS 执行定点修改。

        Parameters
        ----------
        srs_content  : 当前 SRS.md 全文
        issue_content: 审查报告 issue_X.md 全文
        brd_content  : BRD.md（修改时可交叉参考）
        userrd_content: UserRD.md
        syrs_content : SyRS.md
        output_dir   : 输出目录

        Returns
        -------
        str  — 修订后的 SRS.md 文件路径
        """
        logger.info("[Archivist] revise_SRS: applying review fixes...")

        kb_prompt = self.get_knowledge_prompt("revise_SRS")
        prompt = (
            "You are revising an SRS based on a review issue report.\n\n"
            f"{kb_prompt}\n\n"
            "## Current SRS\n"
            f"{srs_content}\n\n"
            "## Review Issue Report\n"
            f"{issue_content}\n\n"
            "## Reference: BRD (first 1500 chars)\n"
            f"{brd_content[:1500]}\n\n"
            "## Reference: UserRD (first 1500 chars)\n"
            f"{userrd_content[:1500]}\n\n"
            "## Reference: SyRS (first 1500 chars)\n"
            f"{syrs_content[:1500]}\n\n"
            "## Instructions\n"
            "1. Address EVERY issue listed in the review report.\n"
            "2. Make **minimal, targeted** changes — do NOT rewrite sections that have no issues.\n"
            "3. Preserve all requirement IDs; do not renumber.\n"
            "4. For each changed section, add an HTML comment: "
            "`<!-- Revised: <brief description> per <issue_id> -->`.\n"
            "5. Update the document header status to indicate the revision round.\n"
            "6. Output the COMPLETE revised SRS as Markdown (because the orchestrator will overwrite the file).\n"
            "7. Do NOT wrap the output in code fences."
        )

        revised = self._call_llm_fresh(prompt, max_tokens=8192)

        # 清理可能的 fence 包裹
        revised = re.sub(r"^```[a-z]*\n?", "", revised)
        revised = re.sub(r"\n?```$", "", revised).strip()

        srs_path = os.path.join(output_dir, "SRS.md")
        self._write_file(srs_path, revised)
        logger.info("[Archivist] Revised SRS.md saved to %s", srs_path)
        print(f"[Archivist] ✓ SRS.md 已修订：{srs_path}")
        return srs_path

    # ------------------------------------------------------------------
    # Memory 管理
    # ------------------------------------------------------------------

    def _compress_memory(self) -> None:
        """保留 system prompt + 首条指令 + 最近 4 条，防止上下文溢出。"""
        recent = self._memory[-4:] if len(self._memory) >= 4 else self._memory[:]
        self._memory = [
            self.llm.format_message("system", self.system_prompt),
            self.llm.format_message(
                "user",
                "Continue writing the next SRS section. Previous sections have been saved.",
            ),
            self.llm.format_message("assistant", "Ready for the next section."),
        ] + recent
        logger.debug("[Archivist] Memory compressed to %d messages.", len(self._memory))

    # ------------------------------------------------------------------
    # BaseAgent 抽象方法
    # ------------------------------------------------------------------

    def process(self, action: str, **kwargs) -> Any:
        """
        支持的 action：
          - "write_SRS"   — 初次撰写 SRS
          - "revise_SRS"  — 根据审查报告修订 SRS
        """
        logger.info("[%s] >>> Executing action: %s", self.agent_name, action)
        if action == "write_SRS":
            return self.write_SRS(
                brd_content=kwargs["brd_content"],
                userrd_content=kwargs["userrd_content"],
                syrs_content=kwargs["syrs_content"],
                output_dir=kwargs["output_dir"],
            )
        elif action == "revise_SRS":
            return self.revise_SRS(
                srs_content=kwargs["srs_content"],
                issue_content=kwargs["issue_content"],
                brd_content=kwargs["brd_content"],
                userrd_content=kwargs["userrd_content"],
                syrs_content=kwargs["syrs_content"],
                output_dir=kwargs["output_dir"],
            )
        else:
            raise ValueError(f"ArchivistAgent: unknown action '{action}'")
