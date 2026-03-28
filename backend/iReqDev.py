from __future__ import annotations

import os
import sys
import logging
from .agent.interviewer import InterviewerAgent
from .agent.human_customer import HumanCustomerAgent
from .agent.analyst import AnalystAgent
from .agent.archivist import ArchivistAgent
from .agent.reviewer import ReviewerAgent
from .agent.human_REngineer import HumanREngineerAgent
from .agent.base import BaseAgent
from .utils.artifact_saver import ArtifactSaver
from .pool.git_artifact_pool import GitArtifactPool, ChangeEntry
from typing import List, Optional


# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("iReDev")

class iReqDevTeam:
    """Main class for the iReDev framework."""
    def __init__(
        self,
        project_name: str,
        output_dir: str,
        config_path,
        human_in_loop: bool = False,
        max_review_rounds: int = 3,
        language: str = "en",
    ):
        self.project_name = project_name
        self.output_dir = output_dir
        self.config_path = config_path
        self.human_in_loop = human_in_loop
        self.max_review_rounds = max_review_rounds
        self.language = language  # "zh" for Chinese, "en" for English
        self.customer = HumanCustomerAgent(config_path=config_path, language=language)
        self.interview_agent = InterviewerAgent(
            name="Interviewer",
            prompt_path="backend/prompt/interviewer/system_prompt.txt",
            config_path=config_path,
            language=language,
        )
        self.analyst_agent = AnalystAgent(
            name="Analyst",
            prompt_path="backend/prompt/analyst/system_prompt.txt",
            config_path=config_path,
            language=language,
        )
        self.archivist_agent = ArchivistAgent(
            name="Archivist",
            prompt_path="backend/prompt/archivist/system_prompt.txt",
            config_path=config_path,
            language=language,
        )
        self.reviewer_agent = ReviewerAgent(
            name="Reviewer",
            prompt_path="backend/prompt/reviewer/system_prompt.txt",
            config_path=config_path,
            language=language,
        )
        # 人在回路需求工程师（仅在 human_in_loop=True 时实例化；不需要 LLM）
        self.human_re_agent: HumanREngineerAgent | None = None
        if self.human_in_loop:
            self.human_re_agent = HumanREngineerAgent()
        self.artifact_saver = ArtifactSaver()
        # 制品池（延迟初始化，run() 中创建）
        self._pool: GitArtifactPool | None = None

    # ------------------------------------------------------------------
    # 制品池事件处理器
    # ------------------------------------------------------------------

    def _on_project_description_created(self, changes: List[ChangeEntry]) -> None:
        """
        订阅回调：当 customer_project_description.md 新建时触发。
        执行 interview_with_customer action。
        """
        for entry in changes:
            if entry.change_type != "created":
                continue
            if not entry.path.endswith("customer_project_description.md"):
                continue

            logger.info("[iReqDev] Detected new artifact: %s — starting interview.", entry.path)
            project_description = entry.content or ""
            if not project_description.strip():
                logger.warning("[iReqDev] customer_project_description.md is empty, skipping.")
                continue

            self.interview_agent.process(
                "interview_with_customer",
                project_description=project_description,
                human_customer=self.customer,
                output_dir=self._project_output_dir,
                max_turns=self.interview_agent.max_turns,
            )

    def _on_dialogue_created(self, changes: List[ChangeEntry]) -> None:
        """
        订阅回调：当 customer_dialogue.md 新建时触发。
        执行 generate_BRD action。
        """
        for entry in changes:
            if entry.change_type != "created":
                continue
            if not entry.path.endswith("customer_dialogue.md"):
                continue

            logger.info("[iReqDev] Detected new artifact: %s — generating BRD.", entry.path)
            dialogue_content = entry.content or ""
            if not dialogue_content.strip():
                # fallback: 从磁盘读取
                full_path = os.path.join(self._project_output_dir, entry.path)
                if os.path.isfile(full_path):
                    with open(full_path, "r", encoding="utf-8") as f:
                        dialogue_content = f.read()

            self.interview_agent.process(
                "generate_BRD",
                dialogue_content=dialogue_content,
                output_dir=self._project_output_dir,
            )

    def _on_brd_created(self, changes: List[ChangeEntry]) -> None:
        """
        订阅回调：当 BRD.md 新建时触发。
        执行 capture_persona action（识别用户角色 + 生成上下文图）。
        """
        for entry in changes:
            if entry.change_type != "created":
                continue
            if not entry.path.endswith("BRD.md"):
                continue

            logger.info("[iReqDev] Detected new artifact: %s — capturing personas.", entry.path)
            brd_content = entry.content or ""
            if not brd_content.strip():
                full_path = os.path.join(self._project_output_dir, entry.path)
                if os.path.isfile(full_path):
                    with open(full_path, "r", encoding="utf-8") as f:
                        brd_content = f.read()

            # 人在回路审查 BRD.md（由 InterviewerAgent 负责修订）
            brd_content = self._human_review(
                "BRD.md", brd_content, self.interview_agent,
            )

            self.interview_agent.process(
                "capture_persona",
                brd_content=brd_content,
                output_dir=self._project_output_dir,
            )

    def _on_userlist_created(self, changes: List[ChangeEntry]) -> None:
        """
        订阅回调：当 UserList.md 新建时触发。
        执行 interview_with_enduser action（对每个角色进行 LLM 访谈）。
        """
        for entry in changes:
            if entry.change_type != "created":
                continue
            if not entry.path.endswith("UserList.md"):
                continue

            logger.info("[iReqDev] Detected new artifact: %s — interviewing endusers.", entry.path)
            userlist_content = entry.content or ""
            if not userlist_content.strip():
                full_path = os.path.join(self._project_output_dir, entry.path)
                if os.path.isfile(full_path):
                    with open(full_path, "r", encoding="utf-8") as f:
                        userlist_content = f.read()

            # 人在回路审查 UserList.md（由 InterviewerAgent 负责修订）
            userlist_content = self._human_review(
                "UserList.md", userlist_content, self.interview_agent,
            )

            # 读取 BRD.md 作为项目上下文传给 EndUserAgent
            brd_content = ""
            brd_path = os.path.join(self._project_output_dir, "BRD.md")
            if os.path.isfile(brd_path):
                with open(brd_path, "r", encoding="utf-8") as f:
                    brd_content = f.read()

            self.interview_agent.process(
                "interview_with_enduser",
                userlist_content=userlist_content,
                output_dir=self._project_output_dir,
                config_path=self.config_path,
                brd_content=brd_content,
            )

    def _on_enduser_dialogue_created(self, changes: List[ChangeEntry]) -> None:
        """
        订阅回调：当 enduser_dialogue.md 新建时触发。
        执行 write_userRD action（撰写用户需求文档）。
        """
        for entry in changes:
            if entry.change_type != "created":
                continue
            if not entry.path.endswith("enduser_dialogue.md"):
                continue

            logger.info("[iReqDev] Detected new artifact: %s — writing UserRD.", entry.path)
            dialogue_content = entry.content or ""
            if not dialogue_content.strip():
                full_path = os.path.join(self._project_output_dir, entry.path)
                if os.path.isfile(full_path):
                    with open(full_path, "r", encoding="utf-8") as f:
                        dialogue_content = f.read()

            self.interview_agent.process(
                "write_userRD",
                enduser_dialogue_content=dialogue_content,
                output_dir=self._project_output_dir,
            )

    def _on_userrd_created(self, changes: List[ChangeEntry]) -> None:
        """
        订阅回调：当 UserRD.md 新建时触发。
        执行 requirements_modeling action（生成用例图）。
        """
        for entry in changes:
            if entry.change_type != "created":
                continue
            if not entry.path.endswith("UserRD.md"):
                continue

            logger.info("[iReqDev] Detected new artifact: %s — generating use case diagram.", entry.path)
            userrd_content = entry.content or ""
            if not userrd_content.strip():
                full_path = os.path.join(self._project_output_dir, entry.path)
                if os.path.isfile(full_path):
                    with open(full_path, "r", encoding="utf-8") as f:
                        userrd_content = f.read()

            # 人在回路审查 UserRD.md（由 InterviewerAgent 负责修订）
            userrd_content = self._human_review(
                "UserRD.md", userrd_content, self.interview_agent,
            )

            self.analyst_agent.process(
                "requirements_modeling",
                userrd_content=userrd_content,
                output_dir=self._project_output_dir,
            )

    def _on_use_case_diagram_created(self, changes: List[ChangeEntry]) -> None:
        """
        订阅回调：当 use_case_diagram.png 新建时触发。
        执行 requirements_analysis action（抽取分类需求 + 撰写 SyRS）。
        注：PNG 内容为二进制，直接从磁盘读取 UserRD.md 文本。
        """
        for entry in changes:
            if entry.change_type != "created":
                continue
            if not entry.path.endswith("use_case_diagram.png"):
                continue

            logger.info("[iReqDev] Detected new artifact: %s — running requirements analysis.", entry.path)
            userrd_path = os.path.join(self._project_output_dir, "UserRD.md")
            if not os.path.isfile(userrd_path):
                logger.warning("[iReqDev] UserRD.md not found at %s; skipping SyRS generation.", userrd_path)
                return

            with open(userrd_path, "r", encoding="utf-8") as f:
                userrd_content = f.read()

            self.analyst_agent.process(
                "requirements_analysis",
                userrd_content=userrd_content,
                output_dir=self._project_output_dir,
            )

    def _on_syrs_created(self, changes: List[ChangeEntry]) -> None:
        """
        订阅回调：当 SyRS.md 新建时触发。
        执行 Archivist write_SRS，然后进入 Review ↔ Revise 循环。
        """
        for entry in changes:
            if entry.change_type != "created":
                continue
            if not entry.path.endswith("SyRS.md"):
                continue

            logger.info("[iReqDev] Detected new artifact: %s — composing SRS.", entry.path)

            # ── 读取上游文档 ──────────────────────────────────────────────
            brd_content = self._read_artifact("BRD.md")
            userrd_content = self._read_artifact("UserRD.md")
            syrs_content = entry.content or self._read_artifact("SyRS.md")

            if not syrs_content:
                logger.warning("[iReqDev] SyRS.md is empty. Skipping SRS generation.")
                return

            # 人在回路审查 SyRS.md（由 AnalystAgent 负责修订）
            syrs_content = self._human_review(
                "SyRS.md", syrs_content, self.analyst_agent,
            )

            # ── 1. Archivist 撰写初版 SRS ─────────────────────────────────
            self.archivist_agent.process(
                "write_SRS",
                brd_content=brd_content,
                userrd_content=userrd_content,
                syrs_content=syrs_content,
                output_dir=self._project_output_dir,
            )
            if self._pool:
                self._pool.auto_commit("chore: initial SRS.md")

            # ── 2. Review ↔ Revise 循环 ───────────────────────────────────
            self._review_revise_loop(
                brd_content=brd_content,
                userrd_content=userrd_content,
                syrs_content=syrs_content,
            )

            # ── 3. 人在回路最终审查 SRS.md（由 ArchivistAgent 负责修订）
            srs_content = self._read_artifact("SRS.md")
            if srs_content:
                self._human_review(
                    "SRS.md", srs_content, self.archivist_agent,
                )

    # ------------------------------------------------------------------
    # Review ↔ Revise 循环
    # ------------------------------------------------------------------

    def _review_revise_loop(
        self,
        brd_content: str,
        userrd_content: str,
        syrs_content: str,
    ) -> None:
        """
        Reviewer 审查 → Archivist 修订 的迭代循环。

        停止条件（满足任一即停）：
          - Reviewer 判定 APPROVED（无 Critical/Major 问题）
          - 达到 self.max_review_rounds 最大轮次
        """
        for round_num in range(1, self.max_review_rounds + 1):
            logger.info(
                "[iReqDev] === Review-Revise Round %d / %d ===",
                round_num, self.max_review_rounds,
            )
            print(f"\n{'='*60}")
            print(f"  Review-Revise Round {round_num} / {self.max_review_rounds}")
            print(f"{'='*60}")

            # 读取当前 SRS
            srs_content = self._read_artifact("SRS.md")
            if not srs_content:
                logger.error("[iReqDev] SRS.md not found — cannot review.")
                return

            # ── Reviewer 审查 ─────────────────────────────────────────────
            issue_path, approved = self.reviewer_agent.process(
                "review_SRS",
                srs_content=srs_content,
                output_dir=self._project_output_dir,
                round_number=round_num,
            )
            if self._pool:
                self._pool.auto_commit(f"chore: review round {round_num}")

            if approved:
                logger.info("[iReqDev] SRS APPROVED at round %d.", round_num)
                print(f"\n[iReqDev] ✓ SRS 通过审查（第 {round_num} 轮）！")
                return

            # 最后一轮仍未通过 → 不再修订，直接退出
            if round_num == self.max_review_rounds:
                logger.info(
                    "[iReqDev] Max review rounds (%d) reached. Stopping.",
                    self.max_review_rounds,
                )
                print(
                    f"\n[iReqDev] ⚠ 已达最大审查轮次 ({self.max_review_rounds})，"
                    f"最新审查报告见 {issue_path}"
                )
                return

            # ── Archivist 修订 ────────────────────────────────────────────
            issue_content = self._read_artifact(os.path.basename(issue_path))
            self.archivist_agent.process(
                "revise_SRS",
                srs_content=srs_content,
                issue_content=issue_content,
                brd_content=brd_content,
                userrd_content=userrd_content,
                syrs_content=syrs_content,
                output_dir=self._project_output_dir,
            )
            if self._pool:
                self._pool.auto_commit(f"chore: SRS revision round {round_num}")

    # ------------------------------------------------------------------
    # 通用辅助
    # ------------------------------------------------------------------

    def _read_artifact(self, filename: str) -> str:
        """从项目输出目录读取制品文件内容。找不到时返回空字符串。"""
        path = os.path.join(self._project_output_dir, filename)
        if not os.path.isfile(path):
            logger.warning("[iReqDev] Artifact not found: %s", path)
            return ""
        with open(path, "r", encoding="utf-8") as f:
            return f.read()

    def _human_review(
        self,
        artifact_name: str,
        content: str,
        producing_agent: BaseAgent,
    ) -> str:
        """
        若 human_in_loop 开启，收集人工反馈并由生成该制品的 agent 修订。

        流程：
          1. 提示查看路径 → 收集反馈
          2. 若无反馈 → 通过
          3. 若有反馈 → producing_agent LLM 修订 → 覆写文件
          4. 提示修改完成 → 问 Yes/No 还有意见吗
          5. No → 通过；Yes → 回到 1

        Returns
        -------
        str — 最终通过的制品内容
        """
        if not self.human_in_loop or self.human_re_agent is None:
            return content

        artifact_path = os.path.join(self._project_output_dir, artifact_name)
        logger.info("[iReqDev] Human-in-loop review for %s", artifact_name)

        for _ in range(self.human_re_agent.max_feedback_rounds):
            feedback = self.human_re_agent.collect_feedback(
                artifact_name, artifact_path,
            )
            if feedback is None:
                # 首次即无意见 → 通过
                print(f"[HumanRE] ✓ {artifact_name} 已通过人工审查。")
                return content

            # 由生成该制品的 agent 根据反馈修订
            content = self._revise_with_feedback(
                producing_agent, artifact_name, content, feedback,
            )
            # 覆写磁盘文件
            self._write_artifact(artifact_name, content)
            if self._pool:
                self._pool.auto_commit(
                    f"chore: human feedback revision — {artifact_name}"
                )

            # 问是否还有意见
            if not self.human_re_agent.ask_has_more_feedback(artifact_name):
                print(f"[HumanRE] ✓ {artifact_name} 已通过人工审查。")
                return content

        # 达到最大反馈轮次
        logger.warning(
            "[iReqDev] Max feedback rounds reached for %s. Proceeding.",
            artifact_name,
        )
        print(
            f"[HumanRE] ⚠ 已达最大反馈轮次 "
            f"({self.human_re_agent.max_feedback_rounds})，"
            f"{artifact_name} 将按当前版本继续。"
        )
        return content

    @staticmethod
    def _revise_with_feedback(
        agent: BaseAgent,
        artifact_name: str,
        content: str,
        feedback: str,
    ) -> str:
        """
        使用 agent 的 LLM 根据人工反馈修订制品。
        不影响 agent 的 memory。
        """
        import re as _re

        prompt = (
            f"You are revising a requirements artifact ({artifact_name}) "
            f"based on feedback from a human requirements engineer.\n\n"
            f"## Current Artifact Content\n\n{content}\n\n"
            f"## Human Feedback\n\n{feedback}\n\n"
            "## Instructions\n"
            "1. Address EVERY point raised in the human feedback.\n"
            "2. Make targeted changes — do NOT rewrite sections unrelated to the feedback.\n"
            "3. Preserve the document structure, heading hierarchy, and requirement IDs.\n"
            "4. If the feedback requests adding new content, integrate it naturally.\n"
            "5. Output the COMPLETE revised artifact as Markdown.\n"
            "6. Do NOT wrap the output in code fences.\n"
            "7. Do NOT include any explanation — only the revised document."
        )

        messages = [
            agent.llm.format_message("system", agent.system_prompt),
            agent.llm.format_message("user", prompt),
        ]
        revised = agent.llm.generate(
            messages=messages,
            temperature=agent.llm_params.get("temperature", 0.15),
            max_output_tokens=agent.llm_params.get("max_output_tokens", 8192),
        ).strip()

        # 清理 fence
        revised = _re.sub(r"^```[a-z]*\n?", "", revised)
        revised = _re.sub(r"\n?```$", "", revised).strip()
        return revised

    def _write_artifact(self, filename: str, content: str) -> None:
        """将制品内容覆写到项目输出目录。"""
        path = os.path.join(self._project_output_dir, filename)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------

    def run(self):
        """Run the iReDev framework."""
        if not os.path.exists(self.output_dir):
            raise FileExistsError(f"Output directory {self.output_dir} does not exist. Please create it or specify a valid output directory.")
        stanitized_project_name = ''.join(c if c.isalnum() else '_' for c in self.project_name)
        project_output_dir = os.path.join(self.output_dir, stanitized_project_name)
        os.makedirs(project_output_dir, exist_ok=True)
        self._project_output_dir = project_output_dir

        logger.info(f"Running iReDev for project '{self.project_name}' with output directory '{project_output_dir}' and config '{self.config_path}'")
        print("Welcome to the iReDev requirements development team! Please provide your simple project description to start the requirements development process.")

        # ── 初始化制品池并注册订阅 ────────────────────────────────────────
        self._pool = GitArtifactPool(directory=project_output_dir, auto_init=True)

        # 订阅 1: customer_project_description.md 新建 → 启动客户访谈
        self._pool.subscribe(
            self._on_project_description_created,
            change_types={"created"},
            path_pattern="customer_project_description.md",
        )
        # 订阅 2: customer_dialogue.md 新建 → 生成 BRD
        self._pool.subscribe(
            self._on_dialogue_created,
            change_types={"created"},
            path_pattern="customer_dialogue.md",
        )
        # 订阅 3: BRD.md 新建 → 识别用户角色并生成上下文图
        self._pool.subscribe(
            self._on_brd_created,
            change_types={"created"},
            path_pattern="BRD.md",
        )
        # 订阅 4: UserList.md 新建 → 对每个 enduser 进行访谈
        self._pool.subscribe(
            self._on_userlist_created,
            change_types={"created"},
            path_pattern="UserList.md",
        )
        # 订阅 5: enduser_dialogue.md 新建 → 撰写 UserRD
        self._pool.subscribe(
            self._on_enduser_dialogue_created,
            change_types={"created"},
            path_pattern="enduser_dialogue.md",
        )
        # 订阅 6: UserRD.md 新建 → 生成用例图
        self._pool.subscribe(
            self._on_userrd_created,
            change_types={"created"},
            path_pattern="UserRD.md",
        )
        # 订阅 7: use_case_diagram.png 新建 → 需求分析 + 撰写 SyRS
        self._pool.subscribe(
            self._on_use_case_diagram_created,
            change_types={"created"},
            path_pattern="use_case_diagram.png",
        )
        # 订阅 8: SyRS.md 新建 → 撰写 SRS + Review ↔ Revise 循环
        self._pool.subscribe(
            self._on_syrs_created,
            change_types={"created"},
            path_pattern="SyRS.md",
        )

        # 启动制品池后台监测
        self._pool.start_watch(interval=1.0)
        logger.info("[iReqDev] Artifact pool watching: %s", project_output_dir)

        # ── 收集初始项目描述并写入制品池 ─────────────────────────────────
        project_desc = self.customer.answer()  # 人工输入，无候选
        self.artifact_saver.write(
            title="customer_project_description",
            content=project_desc,
            directory=project_output_dir,
        )
        logger.info("[iReqDev] customer_project_description.md written.")

        # ── 等待完整 pipeline 完成（SRS.md 出现或用户中断） ────────────
        srs_path = os.path.join(project_output_dir, "SRS.md")
        try:
            import time
            while not os.path.isfile(srs_path):
                time.sleep(1.0)
            # SRS.md 存在后，review-revise 循环在回调中同步执行，
            # 等待 issue 文件作为最终完成标志
            # 给循环足够时间完成（最多等待 review_rounds * 120 秒）
            max_wait = self.max_review_rounds * 120
            waited = 0
            while waited < max_wait:
                # 检查是否有 APPROVED 的 issue 或已达最大轮次
                issue_files = sorted(
                    f for f in os.listdir(project_output_dir)
                    if f.startswith("issue_") and f.endswith(".md")
                )
                if issue_files:
                    latest_issue = os.path.join(project_output_dir, issue_files[-1])
                    with open(latest_issue, "r", encoding="utf-8") as f:
                        content = f.read()
                    if "**APPROVED**" in content:
                        break
                    if len(issue_files) >= self.max_review_rounds:
                        break
                time.sleep(2.0)
                waited += 2

            logger.info("[iReqDev] Pipeline complete.")
            print(f"\n{'='*60}")
            print(f"  iReDev 全流程需求开发完成！")
            print(f"{'='*60}")
            print(f"  BRD              : {os.path.join(project_output_dir, 'BRD.md')}")
            print(f"  UserRD           : {os.path.join(project_output_dir, 'UserRD.md')}")
            print(f"  用例图 (PlantUML) : {os.path.join(project_output_dir, 'use_case_diagram.puml')}")
            print(f"  用例图 (PNG)      : {os.path.join(project_output_dir, 'use_case_diagram.png')}")
            print(f"  SyRS             : {os.path.join(project_output_dir, 'SyRS.md')}")
            print(f"  SRS              : {srs_path}")
            # 列出所有 issue 文件
            issue_files = sorted(
                f for f in os.listdir(project_output_dir)
                if f.startswith("issue_") and f.endswith(".md")
            )
            for issue_f in issue_files:
                print(f"  审查报告          : {os.path.join(project_output_dir, issue_f)}")
        except KeyboardInterrupt:
            logger.info("[iReqDev] Interrupted by user.")
        finally:
            if self._pool:
                self._pool.stop_watch()
                logger.info("[iReqDev] Artifact pool stopped.")

        
