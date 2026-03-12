"""
AnalystAgent — 需求分析代理

职责（由制品池事件触发）：
  1. UserRD.md 新建   → requirements_modeling
       基于用户需求写 PlantUML 用例图 → use_case_diagram.puml / .png
  2. use_case_diagram.png 新建 → requirements_analysis
       a. 从 UserRD.md 抽取并分类系统需求（FR / QA / 约束 / 业务规则）
       b. 以分析结果为上下文，按 SyRS 模版分章节撰写 SyRS.md
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

logger = logging.getLogger(__name__)

_SYRS_TEMPLATE_PATH   = "backend/knowledge/SyRS_template.md"
_SYRS_TEMPLATE_PATH_ZH = "backend/knowledge/SyRS_template_zh.md"
_DEFAULT_PROMPT_PATH  = "backend/prompt/analyst/system_prompt.txt"
_PLANTUML_API         = "https://www.plantuml.com/plantuml/png/"
_PLANTUML_CHARS       = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz-_"


# ---------------------------------------------------------------------------
# PlantUML 工具函数（纯函数，无外部依赖）
# ---------------------------------------------------------------------------

def _plantuml_encode(text: str) -> str:
    """将 PlantUML 源码压缩并编码为公共 API 可接受的 URL 片段。"""
    compressed = zlib.compress(text.encode("utf-8"), 9)[2:-4]
    result: List[str] = []
    i = 0
    while i < len(compressed):
        b1 = compressed[i]
        b2 = compressed[i + 1] if i + 1 < len(compressed) else 0
        b3 = compressed[i + 2] if i + 2 < len(compressed) else 0
        result.append(_PLANTUML_CHARS[(b1 >> 2) & 0x3F])
        result.append(_PLANTUML_CHARS[((b1 & 0x3) << 4) | ((b2 >> 4) & 0xF)])
        result.append(_PLANTUML_CHARS[((b2 & 0xF) << 2) | ((b3 >> 6) & 0x3)])
        result.append(_PLANTUML_CHARS[b3 & 0x3F])
        i += 3
    return "".join(result)


def _render_plantuml(puml_text: str, output_path: str) -> None:
    """
    调用 PlantUML 公共 API 将源码渲染为 PNG 并保存到磁盘。

    Raises
    ------
    RuntimeError
        若 HTTP 请求失败。
    """
    encoded = _plantuml_encode(puml_text)
    url = _PLANTUML_API + encoded
    logger.debug("[AnalystAgent] Requesting PlantUML: %s", url[:100])

    req = urllib.request.Request(
        url, headers={"User-Agent": "iReDev-AnalystAgent/1.0"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        if resp.status != 200:
            raise RuntimeError(f"PlantUML API returned HTTP {resp.status}")
        png_data = resp.read()

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "wb") as f:
        f.write(png_data)


# ---------------------------------------------------------------------------
# AnalystAgent
# ---------------------------------------------------------------------------

class AnalystAgent(BaseAgent):
    """
    Requirements Analyst Agent.

    触发方式（由 iReqDev 的制品池订阅调用 process()）：
      - process("requirements_modeling", ...)
      - process("requirements_analysis", ...)
    """

    def __init__(
        self,
        name: str = "Analyst",
        prompt_path: str = _DEFAULT_PROMPT_PATH,
        config_path: Optional[str] = None,
        syrs_template_path: str = _SYRS_TEMPLATE_PATH,
        language: str = "en",
    ):
        super().__init__(name=name, prompt_path=prompt_path, config_path=config_path, language=language)
        # Select template path based on language
        if language == "zh":
            self.syrs_template_path = _SYRS_TEMPLATE_PATH_ZH
        else:
            self.syrs_template_path = syrs_template_path
        self._syrs_template: Optional[str] = None

    # ------------------------------------------------------------------
    # 属性 / 工具
    # ------------------------------------------------------------------

    @property
    def syrs_template(self) -> str:
        if self._syrs_template is None:
            with open(self.syrs_template_path, "r", encoding="utf-8") as f:
                self._syrs_template = f.read()
        return self._syrs_template

    def _write_file(self, path: str, content: str, mode: str = "w") -> None:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, mode, encoding="utf-8" if "b" not in mode else None) as f:
            f.write(content)

    def _write_binary(self, path: str, data: bytes) -> None:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)

    def _call_llm_fresh(self, user_prompt: str, max_output_tokens: Optional[int] = None) -> str:
        """
        使用全新 messages（system_prompt + 单条 user_prompt）调用 LLM。
        不污染 self._memory，适合独立的单次任务。
        """
        messages = [
            self.llm.format_message("system", self.system_prompt),
            self.llm.format_message("user", user_prompt + self.lang_reminder),
        ]
        return self.llm.generate(
            messages=messages,
            temperature=self.llm_params.get("temperature", 0.2),
            max_output_tokens=max_output_tokens or self.llm_params.get("max_output_tokens", 4096),
        ).strip()

    # ------------------------------------------------------------------
    # Action 1: requirements_modeling
    # ------------------------------------------------------------------

    def requirements_modeling(
        self,
        userrd_content: str,
        output_dir: str,
    ) -> Tuple[str, str]:
        """
        基于 UserRD.md 生成 PlantUML 用例图，渲染为 PNG 并保存到制品池。

        Parameters
        ----------
        userrd_content:
            UserRD.md 的完整文本内容
        output_dir:
            制品输出目录

        Returns
        -------
        Tuple[puml_path, png_path]
        """
        logger.info("[Analyst] requirements_modeling: generating use case diagram...")

        # ── 生成 PlantUML 用例图源码 ──────────────────────────────────────
        puml_text = self._generate_use_case_puml(userrd_content)

        # ── 保存 .puml ────────────────────────────────────────────────────
        puml_path = os.path.join(output_dir, "use_case_diagram.puml")
        self._write_file(puml_path, puml_text)
        logger.info("[Analyst] use_case_diagram.puml saved.")

        # ── 渲染 PNG ──────────────────────────────────────────────────────
        png_path = os.path.join(output_dir, "use_case_diagram.png")
        try:
            _render_plantuml(puml_text, png_path)
            logger.info("[Analyst] use_case_diagram.png saved.")
            print(f"[Analyst] ✓ 用例图已生成：{png_path}")
        except Exception as exc:
            logger.warning("[Analyst] PlantUML rendering failed: %s", exc)
            print(f"[Analyst] ⚠ 用例图渲染失败（{exc}），已保存 .puml 源文件。")

        return puml_path, png_path

    def _generate_use_case_puml(self, userrd_content: str) -> str:
        """
        调用 LLM 基于 UserRD 生成 PlantUML 用例图。
        返回干净的 PlantUML 源码字符串。
        """
        prompt = f"""You are generating a UML Use Case Diagram for a software system based on its User Requirements Document.

## User Requirements Document (UserRD)
{userrd_content}

## Task
Generate a complete and well-structured PlantUML Use Case Diagram that:
- Defines all actor types identified in the UserRD (use `actor`)
- Lists all use cases as ovals (use `usecase`)
- Shows relationships:
  - `-->` for actor-to-usecase associations
  - `..>` with `<<include>>` for included behaviors
  - `..>` with `<<extend>>` for optional extensions
  - Use `package` or `rectangle` to group related use cases under a system boundary
- Groups actors logically (primary actors on the left, secondary/external on the right)
- Uses meaningful, concise labels

Output ONLY valid PlantUML source code starting with `@startuml` and ending with `@enduml`.
No markdown fences, no explanation."""

        raw = self._call_llm_fresh(prompt, max_output_tokens=2048)

        # 清理可能包裹的 fence
        raw = re.sub(r"^```[a-z]*\n?", "", raw.strip())
        raw = re.sub(r"\n?```$", "", raw).strip()

        # 确保有 @startuml / @enduml 包裹
        if not raw.startswith("@startuml"):
            raw = "@startuml\n" + raw
        if not raw.endswith("@enduml"):
            raw = raw + "\n@enduml"

        return raw

    # ------------------------------------------------------------------
    # Action 2: requirements_analysis
    # ------------------------------------------------------------------

    def requirements_analysis(
        self,
        userrd_content: str,
        output_dir: str,
    ) -> str:
        """
        从 UserRD.md 中抽取并分类系统需求，然后按 SyRS 模版分章节撰写 SyRS.md。

        Parameters
        ----------
        userrd_content:
            UserRD.md 的完整文本内容
        output_dir:
            制品输出目录

        Returns
        -------
        str
            SyRS.md 文件路径
        """
        logger.info("[Analyst] requirements_analysis: extracting and classifying requirements...")

        # ── Step 1: 抽取并分类系统需求 ────────────────────────────────────
        analysis: Dict[str, Any] = self._extract_requirements(userrd_content)
        logger.info(
            "[Analyst] Extracted: %d FR, %d QA, %d constraints, %d business rules",
            len(analysis.get("functional_requirements", [])),
            len(analysis.get("quality_attributes", [])),
            len(analysis.get("constraints", [])),
            len(analysis.get("business_rules", [])),
        )

        # ── Step 2: 以分析结果为上下文，分章节撰写 SyRS.md ──────────────
        syrs_path = self._write_syrs(analysis, userrd_content, output_dir)

        return syrs_path

    def _extract_requirements(self, userrd_content: str) -> Dict[str, Any]:
        """
        调用 LLM 从 UserRD 中抽取并分类系统需求，以结构化 JSON 格式返回。

        Returns
        -------
        dict with keys:
            project_name, system_overview,
            functional_requirements, quality_attributes,
            constraints, business_rules
        """
        prompt = f"""You are a Systems Analyst. Analyze the following User Requirements Document and extract all system requirements, classified into four categories.

## User Requirements Document
{userrd_content}

## Task
Output a JSON object with EXACTLY this structure:

```json
{{
  "project_name": "string",
  "system_overview": "2-3 sentence system description",
  "functional_requirements": [
    {{
      "id": "FR-001",
      "title": "short title",
      "description": "The system shall ...",
      "priority": "High|Medium|Low",
      "source": "UserRD section or user story reference",
      "input": "triggering input or event",
      "processing": "what the system does",
      "output": "result or response"
    }}
  ],
  "quality_attributes": [
    {{
      "id": "PERF-001",
      "category": "Performance|Security|Reliability|Usability|Maintainability|Scalability|Portability",
      "description": "The system shall ... [with measurable target]"
    }}
  ],
  "constraints": [
    {{
      "id": "CON-001",
      "category": "Technology|Regulatory|Resource",
      "description": "constraint description"
    }}
  ],
  "business_rules": [
    {{
      "id": "BR-001",
      "description": "rule description",
      "source": "UserRD reference"
    }}
  ]
}}
```

Rules:
- Extract ALL requirements — be comprehensive, not selective.
- Each functional requirement must be atomic (one testable behavior per item).
- Quality attributes must include measurable targets where inferable; otherwise use [TBD].
- Output ONLY the JSON — no explanation, no markdown fences."""

        raw = self._call_llm_fresh(prompt, max_output_tokens=4096)

        # 清理 fence
        text = re.sub(r"^```[a-z]*\n?", "", raw.strip())
        text = re.sub(r"\n?```$", "", text).strip()

        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            logger.warning("[Analyst] Requirements JSON parse failed (%s). Using raw text fallback.", exc)
            return {
                "project_name": "Unknown",
                "system_overview": raw[:500],
                "functional_requirements": [],
                "quality_attributes": [],
                "constraints": [],
                "business_rules": [],
                "_raw_analysis": raw,
            }

    def _write_syrs(
        self,
        analysis: Dict[str, Any],
        userrd_content: str,
        output_dir: str,
    ) -> str:
        """
        以需求分析结果为上下文，按 SyRS 模版分章节逐段生成 SyRS.md。
        最后拼接完整文档保存。
        """
        logger.info("[Analyst] Composing SyRS sections...")

        # 将分析结果序列化为 LLM 可读的上下文
        analysis_context = json.dumps(analysis, indent=2, ensure_ascii=False)

        # Memory 初始化：system_prompt + 分析结果 + UserRD 摘要
        self.refresh_memory([{"role": "system", "content": self.system_prompt}])
        self.add_to_memory(
            "user",
            f"## Structured Requirements Analysis\n```json\n{analysis_context}\n```\n\n"
            f"## Original UserRD (first 2000 chars)\n{userrd_content[:2000]}…",
        )
        self.add_to_memory(
            "assistant",
            "I have analyzed the requirements. I will now write the SyRS section by section, "
            "using the structured analysis as the authoritative source.",
        )

        # 解析模版章节
        sections = self._parse_syrs_sections(self.syrs_template)
        logger.info("[Analyst] SyRS sections: %s", [s[0] for s in sections])

        written_sections: List[str] = []

        for section_header, section_body in sections:
            logger.info("[Analyst] Writing: %s", section_header)

            prompt = (
                f"Write the following SyRS section based on the structured requirements analysis above.\n\n"
                f"**Section**: {section_header}\n\n"
                f"**Template guidance**:\n{section_body}\n\n"
                "Instructions:\n"
                "- Replace ALL placeholders with content derived from the requirements analysis.\n"
                "- For functional requirements sections, generate one entry per FR item in the analysis.\n"
                "- For quality attributes, use the QA items; include measurable targets.\n"
                "- For constraints and business rules, use the respective analysis items.\n"
                "- Mark genuinely unknown information as `[TBD]`.\n"
                "- Output ONLY the Markdown for this section (starting from the # heading)."
                f"{self.lang_reminder}"
            )

            self.add_to_memory("user", prompt)
            section_content = self.generate_response()
            self.add_to_memory("assistant", section_content)
            written_sections.append(section_content.strip())

            # memory 积累过多时压缩
            if len(self._memory) > 14:
                self._compress_syrs_memory(analysis_context)

        # 拼接完整 SyRS
        header = (
            f"# System Requirements Specification (SyRS)\n\n"
            f"> **Project**: {analysis.get('project_name', '[TBD]')}\n"
            f"> **Generated by**: iReDev AnalystAgent\n"
            f"> **Date**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n---\n\n"
        )
        full_syrs = header + "\n\n---\n\n".join(written_sections)

        syrs_path = os.path.join(output_dir, "SyRS.md")
        self._write_file(syrs_path, full_syrs)
        logger.info("[Analyst] SyRS.md saved to %s", syrs_path)
        print(f"\n[Analyst] ✓ SyRS.md 已生成：{syrs_path}")

        return syrs_path

    @staticmethod
    def _parse_syrs_sections(template: str) -> List[Tuple[str, str]]:
        """
        从 SyRS 模版中按一级标题（# N.）拆分章节。
        返回 [(header, body), ...]。
        """
        sections: List[Tuple[str, str]] = []
        pattern = re.compile(r"(^# \d+\..*$)", re.MULTILINE)
        parts = pattern.split(template)

        i = 1
        while i < len(parts) - 1:
            header = parts[i].strip()
            body = parts[i + 1].strip()
            sections.append((header, body))
            i += 2

        if not sections:
            sections = [("# System Requirements Specification", template)]

        return sections

    def _compress_syrs_memory(self, analysis_context: str) -> None:
        """SyRS 生成期间 memory 压缩：保留 system + 分析摘要 + 最近 4 条。"""
        recent = self._memory[-4:] if len(self._memory) >= 4 else self._memory[:]
        self._memory = [
            self.llm.format_message("system", self.system_prompt),
            self.llm.format_message(
                "user",
                f"[Requirements Analysis - truncated]\n```json\n{analysis_context[:2000]}…\n```"
            ),
            self.llm.format_message("assistant", "Continuing SyRS generation..."),
        ] + recent
        logger.debug("[Analyst] Memory compressed to %d messages.", len(self._memory))

    # ------------------------------------------------------------------
    # BaseAgent 抽象方法
    # ------------------------------------------------------------------

    def process(self, action: str, **kwargs) -> Any:
        """
        支持的 action：
          - "requirements_modeling"  — 生成用例图
          - "requirements_analysis"  — 分析需求 + 撰写 SyRS
        """
        logger.info("[%s] >>> Executing action: %s", self.agent_name, action)
        if action == "requirements_modeling":
            return self.requirements_modeling(
                userrd_content=kwargs["userrd_content"],
                output_dir=kwargs["output_dir"],
            )
        elif action == "requirements_analysis":
            return self.requirements_analysis(
                userrd_content=kwargs["userrd_content"],
                output_dir=kwargs["output_dir"],
            )
        else:
            raise ValueError(f"AnalystAgent: unknown action '{action}'")
