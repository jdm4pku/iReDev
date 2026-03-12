"""
iReDev Web Server — FastAPI + WebSocket 后端

提供：
  - WebSocket /ws/{session_id}  实时双向通信
  - REST API /api/*             制品查询、会话管理
  - Static files /              前端静态文件服务
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import secrets
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from queue import Queue, Empty
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import yaml

# ── 项目路径设置 ──────────────────────────────────────────────────────
PROJECT_ROOT = str(Path(__file__).parent.parent)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from backend.iReqDev import iReqDevTeam
from backend.pool.git_artifact_pool import GitArtifactPool, ChangeEntry

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("iReqDev.server")

# ── 常量 ──────────────────────────────────────────────────────────────
OUTPUT_BASE = os.path.join(PROJECT_ROOT, "output")
CONFIG_PATH = os.path.join(PROJECT_ROOT, "backend", "config", "config.yaml")
FRONTEND_DIR = os.path.join(PROJECT_ROOT, "frontend")

# 制品在流水线中的顺序 ── 用于计算进度
PIPELINE_ARTIFACTS = [
    "customer_project_description.md",
    "customer_dialogue.md",
    "BRD.md",
    "UserList.md",
    "context_diagram.puml",
    "enduser_dialogue.md",
    "UserRD.md",
    "use_case_diagram.puml",
    "SyRS.md",
    "SRS.md",
]

# Agent 显示信息
AGENT_META = {
    "system":      {"label": "System",       "color": "#6b7280"},
    "interviewer": {"label": "Interviewer",   "color": "#3b82f6"},
    "customer":    {"label": "Customer",      "color": "#10b981"},
    "enduser":     {"label": "End User",      "color": "#06b6d4"},
    "analyst":     {"label": "Analyst",       "color": "#8b5cf6"},
    "archivist":   {"label": "Archivist",     "color": "#f59e0b"},
    "reviewer":    {"label": "Reviewer",      "color": "#ef4444"},
    "human_re":    {"label": "RE Engineer",   "color": "#ec4899"},
    "user":        {"label": "You",           "color": "#6366f1"},
}

# =====================================================================
# 用户管理 — 简单的 JSON 文件存储
# =====================================================================

USERS_FILE = os.path.join(PROJECT_ROOT, "data", "users.json")


def _hash_password(password: str, salt: str = "") -> str:
    """使用 SHA-256 + salt 哈希密码。"""
    if not salt:
        salt = secrets.token_hex(16)
    hashed = hashlib.sha256((salt + password).encode("utf-8")).hexdigest()
    return f"{salt}:{hashed}"


def _verify_password(password: str, stored: str) -> bool:
    """验证密码。"""
    salt, hashed = stored.split(":", 1)
    return _hash_password(password, salt) == stored


def _load_users() -> Dict[str, Any]:
    """加载用户数据。"""
    if not os.path.isfile(USERS_FILE):
        return {}
    with open(USERS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_users(users: Dict[str, Any]) -> None:
    """保存用户数据。"""
    os.makedirs(os.path.dirname(USERS_FILE), exist_ok=True)
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(users, f, ensure_ascii=False, indent=2)


# 内存中的 token → username 映射
_auth_tokens: Dict[str, str] = {}


def _get_username_from_token(token: str) -> Optional[str]:
    """从 token 获取用户名。"""
    return _auth_tokens.get(token)


# =====================================================================
# Session — 单个项目会话
# =====================================================================

class ProjectSession:
    """管理单个项目的运行状态和事件队列。"""

    def __init__(self, session_id: str, project_name: str, language: str = "en"):
        self.id = session_id
        self.project_name = project_name
        self.language = language  # "zh" for Chinese, "en" for English
        self.created_at = datetime.now().isoformat(timespec="seconds")
        self.status = "idle"          # idle / running / waiting_input / completed / error
        self.stage = ""               # 当前处理阶段
        self.event_queue: Queue = Queue()
        self.input_queue: Queue = Queue()
        self.messages: List[Dict[str, Any]] = []
        self.artifacts: List[Dict[str, Any]] = []
        self.thread: Optional[threading.Thread] = None
        self.output_dir = ""
        # 用于跟踪 pipeline 完成状态（替代硬编码超时）
        self.completion_event = threading.Event()
        # 已处理的制品集合（防止 watch 线程重复触发回调）
        self.processed_artifacts: set = set()

    def push_event(self, event_type: str, **data):
        event = {"type": event_type, "timestamp": datetime.now().isoformat(timespec="seconds"), **data}
        self.event_queue.put(event)

    def push_message(self, agent: str, content: str, msg_type: str = "message"):
        msg = {
            "id": str(uuid.uuid4())[:8],
            "agent": agent,
            "content": content,
            "msg_type": msg_type,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
        }
        self.messages.append(msg)
        self.push_event("agent_message", **msg)

    def push_artifact(self, name: str, artifact_type: str = "document"):
        art = {
            "name": name,
            "artifact_type": artifact_type,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
        }
        # 去重
        if not any(a["name"] == name for a in self.artifacts):
            self.artifacts.append(art)
        self.push_event("artifact_update", **art)

    def wait_for_input(self, prompt: str, candidates: Optional[List[str]] = None) -> str:
        self.status = "waiting_input"
        self.push_event("input_request", prompt=prompt, candidates=candidates or [])
        result = self.input_queue.get()   # 阻塞直到前端发送输入
        self.status = "running"
        return result

    def provide_input(self, text: str):
        self.input_queue.put(text)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "project_name": self.project_name,
            "created_at": self.created_at,
            "status": self.status,
            "stage": self.stage,
            "language": self.language,
            "artifact_count": len(self.artifacts),
            "message_count": len(self.messages),
        }


# =====================================================================
# Sessions Store
# =====================================================================

sessions: Dict[str, ProjectSession] = {}


def _get_session(session_id: str) -> ProjectSession:
    if session_id not in sessions:
        raise HTTPException(404, f"Session {session_id} not found")
    return sessions[session_id]


# =====================================================================
# Pipeline Runner — 在后台线程中跑 iReqDev
# =====================================================================

def _create_user_config(session_id: str, overrides: Dict[str, str]) -> str:
    """
    基于默认 config.yaml + 用户覆盖项，生成临时配置文件。
    返回临时配置文件路径。
    """
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    llm = config.get("llm", {})
    if overrides.get("api_key"):
        llm["api_key"] = overrides["api_key"]
    if overrides.get("base_url"):
        llm["base_url"] = overrides["base_url"]
    if overrides.get("model"):
        llm["model"] = overrides["model"]
    config["llm"] = llm

    tmp_dir = os.path.join(PROJECT_ROOT, "data", "tmp_configs")
    os.makedirs(tmp_dir, exist_ok=True)
    tmp_path = os.path.join(tmp_dir, f"config_{session_id}.yaml")
    with open(tmp_path, "w", encoding="utf-8") as f:
        yaml.dump(config, f, allow_unicode=True, default_flow_style=False)
    return tmp_path


def _run_pipeline(session: ProjectSession):
    """在后台线程中运行完整 iReqDev 流水线。"""
    try:
        session.status = "running"
        session.stage = "initializing"
        session.push_message("system", f"🚀 项目 **{session.project_name}** 需求开发流程已启动！")

        os.makedirs(OUTPUT_BASE, exist_ok=True)

        # 使用用户自定义 config 或全局默认 config
        config_path = getattr(session, "_custom_config_path", None) or CONFIG_PATH

        team = iReqDevTeam(
            project_name=session.project_name,
            output_dir=OUTPUT_BASE,
            config_path=config_path,
            human_in_loop=False,
            max_review_rounds=3,
            language=session.language,
        )

        sanitized = "".join(c if c.isalnum() else "_" for c in session.project_name)
        project_output_dir = os.path.join(OUTPUT_BASE, sanitized)
        os.makedirs(project_output_dir, exist_ok=True)
        session.output_dir = project_output_dir
        team._project_output_dir = project_output_dir

        # ── 猴子补丁：替换 HumanCustomerAgent 的终端 I/O ────────────────
        _patch_customer(team.customer, session)

        # ── 猴子补丁：为各 agent 注入消息钩子 ───────────────────────────
        _patch_agent_logging(team, session)

        # ── 初始化制品池 ──────────────────────────────────────────────────
        team._pool = GitArtifactPool(directory=project_output_dir, auto_init=True)

        # ── 制品池观察回调：文件变更时推送给前端 ─────────────────────────
        def on_any_change(changes: List[ChangeEntry]):
            for c in changes:
                if c.change_type in ("created", "modified"):
                    atype = c.artifact_type or "document"
                    session.push_artifact(c.path, atype)

        team._pool.subscribe(on_any_change)

        # ── 注册制品池订阅（在独立线程中运行回调，避免阻塞 watch 线程） ──
        def _make_dedup_threaded(callback, artifact_key, is_final=False):
            """包装回调：(1)防重复触发 (2)在独立线程中执行 (3)最终步骤设置完成事件"""
            def wrapper(changes):
                if artifact_key in session.processed_artifacts:
                    return  # 已处理过，跳过
                session.processed_artifacts.add(artifact_key)

                def _run():
                    try:
                        callback(changes)
                    except Exception as exc:
                        logger.exception("[Pipeline] Callback error for %s: %s", artifact_key, exc)
                        session.push_message("system", f"⚠️ 步骤 {artifact_key} 出错: {exc}")
                    finally:
                        if is_final:
                            # 最终步骤完成后，扫描输出目录补推所有未被 watch loop 捕获的制品
                            # （因为 _on_syrs_created 中手动 auto_commit 会让 watch loop 错过 SRS/issue 等文件）
                            _flush_missing_artifacts(session)
                            session.completion_event.set()

                t = threading.Thread(target=_run, daemon=True, name=f"cb-{artifact_key}")
                t.start()
            return wrapper

        team._pool.subscribe(_make_dedup_threaded(team._on_project_description_created, "customer_project_description.md"), change_types={"created"}, path_pattern="customer_project_description.md")
        team._pool.subscribe(_make_dedup_threaded(team._on_dialogue_created, "customer_dialogue.md"), change_types={"created"}, path_pattern="customer_dialogue.md")
        team._pool.subscribe(_make_dedup_threaded(team._on_brd_created, "BRD.md"), change_types={"created"}, path_pattern="BRD.md")
        team._pool.subscribe(_make_dedup_threaded(team._on_userlist_created, "UserList.md"), change_types={"created"}, path_pattern="UserList.md")
        team._pool.subscribe(_make_dedup_threaded(team._on_enduser_dialogue_created, "enduser_dialogue.md"), change_types={"created"}, path_pattern="enduser_dialogue.md")
        team._pool.subscribe(_make_dedup_threaded(team._on_userrd_created, "UserRD.md"), change_types={"created"}, path_pattern="UserRD.md")
        team._pool.subscribe(_make_dedup_threaded(team._on_use_case_diagram_created, "use_case_diagram.png"), change_types={"created"}, path_pattern="use_case_diagram.png")
        # SyRS.md 是最后一步（触发 SRS 撰写 + Review），标记为 is_final
        team._pool.subscribe(_make_dedup_threaded(team._on_syrs_created, "SyRS.md", is_final=True), change_types={"created"}, path_pattern="SyRS.md")

        team._pool.start_watch(interval=2.0)

        # ── 等待用户输入项目描述 ──────────────────────────────────────────
        session.stage = "collecting_description"
        session.push_message("system", "请输入您的项目描述（简洁概括即可），我们将开始需求开发流程。")
        project_desc = session.wait_for_input("请输入您的项目描述：")
        session.push_message("user", project_desc)
        session.push_message("system",
            "✅ 项目描述已收到，正在启动需求访谈...\n\n"
            "📋 **完整流程概览：**\n"
            "1️⃣ 客户访谈 → 准备问题清单，与您交互确认需求\n"
            "2️⃣ 撰写 BRD → 生成业务需求文档\n"
            "3️⃣ 用户角色识别 → 识别终端用户角色，绘制上下文图\n"
            "4️⃣ 终端用户访谈 → 深入每个用户角色的详细需求\n"
            "5️⃣ 撰写 UserRD → 生成用户需求文档\n"
            "6️⃣ 需求建模 → 生成用例图\n"
            "7️⃣ 需求分析 → 抽取分类系统需求，撰写 SyRS\n"
            "8️⃣ 撰写 SRS → 生成软件需求规格说明\n"
            "9️⃣ 质量审查 → 9 维度自动审查 + 迭代修订\n\n"
            "⏳ 流程已启动，请稍候..."
        )

        # 写入制品池 → 自动触发 pipeline
        team.artifact_saver.write(
            title="customer_project_description",
            content=project_desc,
            directory=project_output_dir,
        )

        # ── 等待 pipeline 完成（事件驱动，不再使用硬编码超时） ─────────
        # completion_event 由最后一个回调（_on_syrs_created）在结束时设置
        PIPELINE_TIMEOUT = 7200  # 2 小时上限，防止永久挂起
        finished = session.completion_event.wait(timeout=PIPELINE_TIMEOUT)

        if finished:
            session.status = "completed"
            session.stage = "completed"
            session.push_message("system",
                "🎉 **需求开发全流程完成！**\n\n"
                "所有产出文档已生成，请在右侧制品池中查看：\n"
                "- 📑 BRD（业务需求文档）\n"
                "- 👥 UserList（用户角色列表）\n"
                "- 📝 UserRD（用户需求文档）\n"
                "- 📊 用例图（PlantUML + PNG）\n"
                "- 📋 SyRS（系统需求规格说明）\n"
                "- 📜 SRS（软件需求规格说明）\n"
                "- 🔍 审查报告\n\n"
                "点击右侧制品池中的文件即可查看详细内容。"
            )
            session.push_event("pipeline_complete")
        else:
            session.status = "error"
            session.stage = "timeout"
            session.push_message("system", "⚠️ 流程超时，部分制品可能已生成。请检查右侧制品池。")
            session.push_event("error", message="Pipeline timed out")

        if team._pool:
            team._pool.stop_watch(timeout=10.0)

    except Exception as exc:
        logger.exception("[Pipeline] Error running pipeline for session %s", session.id)
        session.status = "error"
        session.push_message("system", f"❌ 流程运行出错: {exc}")
        session.push_event("error", message=str(exc))


def _flush_missing_artifacts(session: ProjectSession):
    """
    扫描项目输出目录，补推所有未被 watch loop 捕获的制品到前端。

    在流水线最终步骤（_on_syrs_created）中，SRS.md / issue_*.md 等文件
    是通过 iReqDev 手动调用 auto_commit() 提交的，绕过了 watch loop 的
    变更检测——导致 on_any_change 回调从未被触发，前端不显示这些制品。
    本函数在 completion_event 之前做一次补偿扫描。
    """
    output_dir = getattr(session, "output_dir", None)
    if not output_dir or not os.path.isdir(output_dir):
        return

    pushed_names = {a["name"] for a in session.artifacts}
    _ARTIFACT_EXTS = {".md", ".txt", ".puml", ".png", ".svg", ".json", ".yaml", ".yml"}

    for fname in sorted(os.listdir(output_dir)):
        fpath = os.path.join(output_dir, fname)
        if not os.path.isfile(fpath):
            continue
        ext = os.path.splitext(fname)[1].lower()
        if ext not in _ARTIFACT_EXTS:
            continue
        if fname in pushed_names:
            continue

        # 推断制品类型
        if ext in (".puml", ".png", ".svg"):
            atype = "model"
        else:
            atype = "document"

        logger.info("[FlushArtifacts] Pushing missed artifact: %s", fname)
        session.push_artifact(fname, atype)


def _patch_customer(customer, session: ProjectSession):
    """替换 HumanCustomerAgent 的终端交互为 WebSocket 通信。"""

    def patched_answer(question=None):
        if question is None:
            return session.wait_for_input("请输入您的项目描述（简洁概括即可）：")

        # 将问题加入 memory
        customer.add_to_memory("user", f"[Interviewer Question]\n{question}")

        # 生成候选回答
        candidates = {}
        try:
            candidates = customer._generate_candidates()
        except Exception as exc:
            logger.warning("Candidate generation failed: %s", exc)

        candidate_list = []
        if candidates:
            candidate_list = [
                candidates.get("candidate_1", ""),
                candidates.get("candidate_2", ""),
            ]

        # 发送访谈问题给前端
        session.push_message("interviewer", question)

        # 等待用户选择/输入
        reply = session.wait_for_input(question, candidate_list)
        session.push_message("user", reply)

        customer.add_to_memory("assistant", reply)
        return reply

    customer.answer = patched_answer


def _patch_agent_logging(team: iReqDevTeam, session: ProjectSession):
    """为各 agent 的 process 方法添加消息推送钩子。"""

    # 包装 process 方法，在关键步骤推送阶段信息
    original_iv_process = team.interview_agent.process

    def iv_process(action, **kwargs):
        stage_map = {
            "interview_with_customer": (
                "interviewing",
                "🎙 **阶段 1/9：客户访谈**\n\n"
                "正在进行客户访谈，包含以下步骤：\n"
                "- 📋 根据项目描述准备结构化问题清单\n"
                "- 💬 逐一与您交互确认需求细节\n"
                "- 📝 记录完整访谈对话\n\n"
                "请根据提问回答，访谈结束后将自动进入下一阶段。"
            ),
            "generate_BRD": (
                "generating_brd",
                "📝 **阶段 2/9：撰写业务需求文档 (BRD)**\n\n"
                "正在基于访谈记录生成 BRD，包含：\n"
                "- 项目背景与目标\n"
                "- 业务需求清单\n"
                "- 利益相关者分析\n"
                "- 约束条件与假设\n\n"
                "⏳ AI 正在撰写中，请稍候..."
            ),
            "capture_persona": (
                "capturing_persona",
                "👥 **阶段 3/9：用户角色识别**\n\n"
                "正在基于 BRD 识别用户角色，包含：\n"
                "- 提取所有终端用户角色\n"
                "- 建立用户角色列表 (UserList)\n"
                "- 绘制系统上下文图 (Context Diagram)\n\n"
                "⏳ AI 正在分析中，请稍候..."
            ),
            "interview_with_enduser": (
                "interviewing_enduser",
                "🎙 **阶段 4/9：终端用户访谈**\n\n"
                "正在对每个用户角色进行深度访谈：\n"
                "- 👤 逐一访谈各用户角色\n"
                "- 📋 收集详细功能需求与交互期望\n"
                "- 💬 记录用户访谈对话\n\n"
                "⏳ AI 模拟用户回答中，请稍候..."
            ),
            "write_userRD": (
                "writing_userrd",
                "📝 **阶段 5/9：撰写用户需求文档 (UserRD)**\n\n"
                "正在基于终端用户访谈生成 UserRD，包含：\n"
                "- 各角色的详细需求描述\n"
                "- 用户场景与使用流程\n"
                "- 优先级与验收标准\n\n"
                "⏳ AI 正在撰写中，请稍候..."
            ),
        }
        activity_map = {
            "interview_with_customer": ("interviewer", "正在根据项目描述准备访谈问题清单..."),
            "generate_BRD": ("archivist", "正在基于访谈记录撰写业务需求文档 (BRD)..."),
            "capture_persona": ("interviewer", "正在识别终端用户角色并编制用户列表..."),
            "interview_with_enduser": ("interviewer", "正在对各终端用户角色进行深度访谈..."),
            "write_userRD": ("archivist", "正在撰写用户需求文档 (UserRD)..."),
        }
        if action in stage_map:
            session.stage = stage_map[action][0]
            session.push_message("system", stage_map[action][1])
        if action in activity_map:
            a_agent, a_text = activity_map[action]
            session.push_event("agent_activity", agent=a_agent, activity=a_text, status="started")
        result = original_iv_process(action, **kwargs)
        if action in activity_map:
            session.push_event("agent_activity", agent="", activity="", status="completed")
        return result

    team.interview_agent.process = iv_process

    original_an_process = team.analyst_agent.process

    def an_process(action, **kwargs):
        stage_map = {
            "requirements_modeling": (
                "modeling",
                "📊 **阶段 6/9：需求建模**\n\n"
                "正在将需求可视化建模：\n"
                "- 生成 PlantUML 用例图源码\n"
                "- 渲染用例图 (PNG)\n"
                "- 关联 Actor 与 Use Case\n\n"
                "⏳ AI 正在建模中，请稍候..."
            ),
            "requirements_analysis": (
                "analyzing",
                "🔍 **阶段 7/9：需求分析与撰写 SyRS**\n\n"
                "正在进行系统需求分析：\n"
                "- 抽取功能需求与非功能需求\n"
                "- 需求分类与编号\n"
                "- 撰写系统需求规格说明 (SyRS)\n\n"
                "⏳ AI 正在分析中，请稍候..."
            ),
        }
        activity_map = {
            "requirements_modeling": ("analyst", "正在构建需求模型与用例图..."),
            "requirements_analysis": ("analyst", "正在进行需求分析与分类，撰写 SyRS..."),
        }
        if action in stage_map:
            session.stage = stage_map[action][0]
            session.push_message("system", stage_map[action][1])
        if action in activity_map:
            a_agent, a_text = activity_map[action]
            session.push_event("agent_activity", agent=a_agent, activity=a_text, status="started")
        result = original_an_process(action, **kwargs)
        if action in activity_map:
            session.push_event("agent_activity", agent="", activity="", status="completed")
        return result

    team.analyst_agent.process = an_process

    original_ar_process = team.archivist_agent.process

    def ar_process(action, **kwargs):
        stage_map = {
            "write_SRS": (
                "writing_srs",
                "📜 **阶段 8/9：撰写软件需求规格说明 (SRS)**\n\n"
                "正在整合所有上游文档撰写 SRS：\n"
                "- 整合 BRD、UserRD、SyRS\n"
                "- 生成完整需求规格文档\n"
                "- 包含需求追踪矩阵\n\n"
                "⏳ AI 正在撰写中，请稍候..."
            ),
            "revise_SRS": (
                "revising_srs",
                "✏️ **SRS 修订中**\n\n"
                "正在根据审查报告修订 SRS：\n"
                "- 针对 Critical/Major 问题逐一修改\n"
                "- 保持文档结构与需求 ID 不变\n"
                "- 修订后将自动提交新一轮审查\n\n"
                "⏳ AI 正在修订中，请稍候..."
            ),
        }
        activity_map = {
            "write_SRS": ("archivist", "正在整合所有上游文档撰写 SRS..."),
            "revise_SRS": ("archivist", "正在根据审查意见修订 SRS..."),
        }
        if action in stage_map:
            session.stage = stage_map[action][0]
            session.push_message("system", stage_map[action][1])
        if action in activity_map:
            a_agent, a_text = activity_map[action]
            session.push_event("agent_activity", agent=a_agent, activity=a_text, status="started")
        result = original_ar_process(action, **kwargs)
        if action in activity_map:
            session.push_event("agent_activity", agent="", activity="", status="completed")
        return result

    team.archivist_agent.process = ar_process

    original_rv_process = team.reviewer_agent.process

    def rv_process(action, **kwargs):
        if action == "review_SRS":
            round_num = kwargs.get("round_number", "?")
            session.stage = "reviewing"
            session.push_message("system",
                f"🔎 **阶段 9/9：质量审查（第 {round_num} 轮）**\n\n"
                "正在对 SRS 进行 9 维度质量审查：\n"
                "- 完整性、一致性、正确性\n"
                "- 可追踪性、无歧义性、可验证性\n"
                "- 可行性、可维护性、简洁性\n\n"
                "⏳ Reviewer 正在审查中，请稍候..."
            )
            session.push_event("agent_activity", agent="reviewer", activity=f"正在进行第 {round_num} 轮 9 维度质量审查...", status="started")
        result = original_rv_process(action, **kwargs)
        if action == "review_SRS":
            session.push_event("agent_activity", agent="", activity="", status="completed")
        return result

    team.reviewer_agent.process = rv_process


# =====================================================================
# FastAPI App
# =====================================================================

app = FastAPI(title="iReDev Web", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── REST Endpoints ────────────────────────────────────────────────────

# ── 用户认证 ──────────────────────────────────────────────────────────

@app.post("/api/auth/register")
async def register(body: dict):
    """用户注册。"""
    username = (body.get("username") or "").strip()
    password = (body.get("password") or "").strip()
    if not username or not password:
        raise HTTPException(400, "用户名和密码不能为空")
    if len(username) < 2 or len(username) > 30:
        raise HTTPException(400, "用户名长度应在 2-30 个字符之间")
    if len(password) < 4:
        raise HTTPException(400, "密码至少 4 个字符")

    users = _load_users()
    if username in users:
        raise HTTPException(409, "用户名已存在")

    users[username] = {
        "password": _hash_password(password),
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "settings": {},
    }
    _save_users(users)

    # 自动登录
    token = secrets.token_hex(32)
    _auth_tokens[token] = username
    return {"token": token, "username": username}


@app.post("/api/auth/login")
async def login(body: dict):
    """用户登录。"""
    username = (body.get("username") or "").strip()
    password = (body.get("password") or "").strip()
    if not username or not password:
        raise HTTPException(400, "用户名和密码不能为空")

    users = _load_users()
    user = users.get(username)
    if not user or not _verify_password(password, user["password"]):
        raise HTTPException(401, "用户名或密码错误")

    token = secrets.token_hex(32)
    _auth_tokens[token] = username
    return {"token": token, "username": username}


@app.get("/api/auth/me")
async def get_current_user(token: str = ""):
    """获取当前用户信息（通过 query param ?token=...）。"""
    username = _get_username_from_token(token)
    if not username:
        raise HTTPException(401, "未登录或 token 无效")
    users = _load_users()
    user = users.get(username, {})
    return {
        "username": username,
        "has_settings": bool(user.get("settings", {}).get("api_key")),
    }


# ── 用户设置（LLM 配置） ─────────────────────────────────────────────

@app.get("/api/settings")
async def get_settings(token: str = ""):
    """获取用户的 LLM 设置。"""
    username = _get_username_from_token(token)
    if not username:
        raise HTTPException(401, "未登录")
    users = _load_users()
    user = users.get(username, {})
    settings = user.get("settings", {})
    # 隐藏 api_key 的中间部分
    masked = dict(settings)
    if masked.get("api_key"):
        key = masked["api_key"]
        if len(key) > 8:
            masked["api_key"] = key[:4] + "*" * (len(key) - 8) + key[-4:]
        else:
            masked["api_key"] = "****"
    return masked


@app.put("/api/settings")
async def update_settings(body: dict, token: str = ""):
    """更新用户的 LLM 设置。"""
    username = _get_username_from_token(token)
    if not username:
        raise HTTPException(401, "未登录")

    users = _load_users()
    if username not in users:
        raise HTTPException(404, "用户不存在")

    # 提取允许的字段
    allowed_keys = {"api_key", "base_url", "model"}
    new_settings = {}
    for k in allowed_keys:
        val = body.get(k, "").strip() if isinstance(body.get(k), str) else ""
        if val:
            new_settings[k] = val

    users[username]["settings"] = new_settings
    _save_users(users)
    return {"message": "设置已保存", "settings": {k: ("****" if k == "api_key" else v) for k, v in new_settings.items()}}


def _get_user_llm_overrides(token: str) -> Dict[str, str]:
    """从 token 获取用户自定义的 LLM 配置覆盖项。"""
    username = _get_username_from_token(token)
    if not username:
        return {}
    users = _load_users()
    user = users.get(username, {})
    return user.get("settings", {})


# ── 会话管理 ──────────────────────────────────────────────────────────

@app.get("/api/sessions")
async def list_sessions():
    return [s.to_dict() for s in sessions.values()]


@app.post("/api/sessions")
async def create_session(body: dict):
    project_name = body.get("project_name", "Untitled Project")
    language = body.get("language", "en")  # "zh" or "en"
    token = body.get("token", "")
    sid = str(uuid.uuid4())[:8]
    session = ProjectSession(sid, project_name, language=language)

    # 如果用户有自定义 LLM 设置，生成临时 config 文件供本次会话使用
    user_overrides = _get_user_llm_overrides(token)
    if user_overrides.get("api_key"):
        session._custom_config_path = _create_user_config(sid, user_overrides)
    else:
        session._custom_config_path = None

    sessions[sid] = session
    return session.to_dict()


@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str):
    return _get_session(session_id).to_dict()


@app.get("/api/sessions/{session_id}/messages")
async def get_messages(session_id: str):
    session = _get_session(session_id)
    return session.messages


@app.get("/api/sessions/{session_id}/artifacts")
async def get_artifacts(session_id: str):
    session = _get_session(session_id)
    return session.artifacts


@app.get("/api/sessions/{session_id}/artifacts/{artifact_name:path}")
async def get_artifact_content(session_id: str, artifact_name: str):
    session = _get_session(session_id)
    if not session.output_dir:
        raise HTTPException(404, "Project output not available yet")
    fpath = os.path.join(session.output_dir, artifact_name)
    if not os.path.isfile(fpath):
        raise HTTPException(404, f"Artifact '{artifact_name}' not found")
    # 二进制文件（PNG 等）返回 base64
    ext = os.path.splitext(artifact_name)[1].lower()
    if ext in (".png", ".jpg", ".jpeg", ".gif", ".svg"):
        import base64
        with open(fpath, "rb") as f:
            data = base64.b64encode(f.read()).decode()
        return {"name": artifact_name, "type": "image", "content": data, "mime": f"image/{ext.lstrip('.')}"}
    with open(fpath, "r", encoding="utf-8") as f:
        content = f.read()
    return {"name": artifact_name, "type": "text", "content": content}


# ── WebSocket ─────────────────────────────────────────────────────────

@app.websocket("/ws/{session_id}")
async def websocket_endpoint(ws: WebSocket, session_id: str):
    await ws.accept()
    session = sessions.get(session_id)
    if not session:
        await ws.send_json({"type": "error", "message": "Session not found"})
        await ws.close()
        return

    # 发送历史消息 & 制品
    for msg in session.messages:
        await ws.send_json({"type": "agent_message", **msg})
    for art in session.artifacts:
        await ws.send_json({"type": "artifact_update", **art})

    # 如果会话尚未启动，启动 pipeline
    if session.status == "idle":
        session.thread = threading.Thread(target=_run_pipeline, args=(session,), daemon=True)
        session.thread.start()

    # 双向消息循环
    async def send_events():
        """从 event_queue 持续读取事件发给前端。"""
        while True:
            try:
                event = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: session.event_queue.get(timeout=0.3)
                )
                await ws.send_json(event)
            except Empty:
                pass
            except Exception:
                break

    send_task = asyncio.create_task(send_events())

    try:
        while True:
            data = await ws.receive_json()
            msg_type = data.get("type", "")
            if msg_type == "user_input":
                content = data.get("content", "").strip()
                if content:
                    session.provide_input(content)
            elif msg_type == "ping":
                await ws.send_json({"type": "pong"})
    except WebSocketDisconnect:
        logger.info("[WS] Client disconnected from session %s", session_id)
    except Exception as exc:
        logger.warning("[WS] Error in session %s: %s", session_id, exc)
    finally:
        send_task.cancel()


# ── 静态文件 & 入口页面 ──────────────────────────────────────────────

@app.get("/")
async def serve_index():
    return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))


# 挂载静态文件（js, css 等）— 使用 /static 前缀，避免拦截 API 路由
if os.path.isdir(FRONTEND_DIR):
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="frontend")


# ── 启动入口 ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    os.makedirs(OUTPUT_BASE, exist_ok=True)
    print(f"\n{'='*50}")
    print(f"  iReDev Web Server")
    print(f"  http://localhost:8000")
    print(f"{'='*50}\n")
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
