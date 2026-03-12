"""
GitArtifactPool — 基于 Git 的制品池

职责：
  - 将本地目录托管为 Git 仓库
  - 追踪制品（文件）的 新增 / 修改 / 删除 / 重命名 等变化
  - 提供变更内容（diff 或全文）
  - 支持非阻塞后台监测，按文件模式 / 变更类型 向订阅者分发事件
  - 维护内存变更历史，支持自动提交
"""

from __future__ import annotations

import fnmatch
import logging
import os
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, asdict, field
from datetime import datetime
from typing import Callable, Deque, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 异常
# ---------------------------------------------------------------------------

class GitCommandError(RuntimeError):
    """Git 子进程调用失败时抛出。"""

    def __init__(self, command: List[str], returncode: int, stderr: str):
        cmd = " ".join(command)
        super().__init__(
            f"Git command failed (exit {returncode}): {cmd}\n{stderr.strip()}"
        )
        self.command = command
        self.returncode = returncode
        self.stderr = stderr


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------

# Git porcelain status code → 语义类型映射
_STATUS_MAP: Dict[str, str] = {
    "??": "created",    # 未跟踪 (untracked)
    "A":  "created",    # 已添加到暂存区
    "M":  "modified",   # 已修改
    "D":  "deleted",    # 已删除
    "R":  "renamed",    # 已重命名
    "C":  "copied",     # 已复制
    "U":  "conflicted", # 合并冲突
    "T":  "modified",   # 类型变更 (file → symlink 等)
}

# 文件扩展名 → 制品语义类型
_EXT_ARTIFACT_TYPE: Dict[str, str] = {
    ".md":     "document",
    ".txt":    "document",
    ".rst":    "document",
    ".pdf":    "document",
    ".docx":   "document",
    ".py":     "code",
    ".js":     "code",
    ".ts":     "code",
    ".java":   "code",
    ".json":   "data",
    ".yaml":   "data",
    ".yml":    "data",
    ".toml":   "data",
    ".xml":    "data",
    ".csv":    "data",
    ".uml":    "model",
    ".drawio": "model",
    ".puml":   "model",
}


def _detect_artifact_type(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    return _EXT_ARTIFACT_TYPE.get(ext, "other")


@dataclass
class ChangeEntry:
    """单个制品的一次变更记录。"""

    path: str
    """相对于仓库根目录的文件路径（重命名时为新路径）。"""

    old_path: Optional[str]
    """重命名 / 复制时的原始路径，否则为 None。"""

    change_type: str
    """语义变更类型：created / modified / deleted / renamed / copied / conflicted / unknown。"""

    status_code: str
    """Git porcelain 原始状态码，如 'M ', ' M', '??', 'R '。"""

    artifact_type: str
    """制品语义类型：document / code / data / model / other。"""

    content: Optional[str]
    """文件当前完整内容（仅 created / modified 有效；deleted 为 None）。"""

    diff: Optional[str]
    """与上一版本的 unified diff（created 时为全文 diff；deleted 时尝试展示删除内容）。"""

    timestamp: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    """变更被检测到的本地时间，ISO-8601 格式。"""

    def to_dict(self) -> dict:
        return asdict(self)

    def summary(self) -> str:
        action = self.change_type.upper()
        old = f" (← {self.old_path})" if self.old_path else ""
        return f"[{action}] {self.artifact_type} | {self.path}{old} @ {self.timestamp}"


# ---------------------------------------------------------------------------
# 订阅者描述符
# ---------------------------------------------------------------------------

@dataclass
class Subscription:
    """描述一个事件订阅。"""

    callback: Callable[[List[ChangeEntry]], None]
    """收到变更列表时的回调函数（在监测线程中调用，注意线程安全）。"""

    change_types: Optional[Set[str]] = None
    """仅关注的变更类型集合，None 表示全部关注。"""

    path_pattern: Optional[str] = None
    """fnmatch 文件路径过滤模式，None 表示全部文件。如 '*.md' / 'specs/*'。"""

    artifact_types: Optional[Set[str]] = None
    """仅关注的制品类型集合，None 表示全部。如 {'document', 'code'}。"""

    def matches(self, entry: ChangeEntry) -> bool:
        if self.change_types and entry.change_type not in self.change_types:
            return False
        if self.path_pattern and not fnmatch.fnmatch(entry.path, self.path_pattern):
            return False
        if self.artifact_types and entry.artifact_type not in self.artifact_types:
            return False
        return True


# ---------------------------------------------------------------------------
# 核心制品池
# ---------------------------------------------------------------------------

class GitArtifactPool:
    """
    基于 Git 的制品池。

    用法::

        pool = GitArtifactPool("/path/to/artifacts")

        # 订阅所有 Markdown 文档变更
        def on_doc_change(changes):
            for c in changes:
                print(c.summary())

        sub_id = pool.subscribe(on_doc_change, path_pattern="*.md")

        pool.start_watch()          # 非阻塞后台监测
        ...
        pool.stop_watch()           # 停止监测
        pool.unsubscribe(sub_id)    # 取消订阅
    """

    def __init__(
        self,
        directory: str,
        auto_init: bool = True,
        history_maxlen: int = 200,
    ):
        """
        Parameters
        ----------
        directory:
            被监测的本地目录路径。
        auto_init:
            若目录不是 Git 仓库，是否自动初始化并创建初始空提交。
        history_maxlen:
            内存中保留的最大历史变更条目数。
        """
        self.directory = os.path.abspath(directory)
        if not os.path.isdir(self.directory):
            raise FileNotFoundError(f"Directory does not exist: {self.directory}")

        self._history: Deque[ChangeEntry] = deque(maxlen=history_maxlen)
        self._subscriptions: Dict[int, Subscription] = {}
        self._sub_counter = 0
        self._sub_lock = threading.Lock()

        self._watch_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._ready_event = threading.Event()  # 初始快照已就绪信号

        if auto_init:
            self._init_repo_if_needed()

    # ------------------------------------------------------------------
    # Git 底层封装
    # ------------------------------------------------------------------

    def _run_git(self, args: List[str], check: bool = True) -> str:
        """运行任意 git 子命令，返回 stdout 字符串。"""
        command = ["git", *args]
        result = subprocess.run(
            command,
            cwd=self.directory,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if check and result.returncode != 0:
            raise GitCommandError(command, result.returncode, result.stderr)
        return result.stdout

    # ------------------------------------------------------------------
    # 仓库初始化
    # ------------------------------------------------------------------

    def is_repo(self) -> bool:
        """检查目录是否已是 Git 仓库。"""
        return os.path.isdir(os.path.join(self.directory, ".git"))

    def _init_repo_if_needed(self) -> bool:
        """
        若目录尚未初始化为 Git 仓库，则执行 git init 并创建一个空的初始提交，
        以确保后续 diff 命令能正常运行。

        Returns
        -------
        bool
            True 表示执行了初始化，False 表示已经是仓库。
        """
        if self.is_repo():
            return False

        self._run_git(["init"])

        # 创建初始空提交（确保 HEAD 存在，使 diff 命令不报错）
        self._run_git(["commit", "--allow-empty", "-m", "chore: initialize artifact pool"])
        logger.info("Initialized Git repository at %s", self.directory)
        return True

    # ------------------------------------------------------------------
    # 变更检测
    # ------------------------------------------------------------------

    def _status_porcelain(self) -> str:
        return self._run_git(["status", "--porcelain", "-uall"])

    @staticmethod
    def _map_change_type(status_code: str) -> str:
        if status_code == "??":
            return "created"
        index_flag = status_code[0].strip()
        worktree_flag = status_code[1].strip()
        for flag in (index_flag, worktree_flag):
            if flag in _STATUS_MAP:
                return _STATUS_MAP[flag]
        return "unknown"

    def _parse_porcelain(self, porcelain: str) -> List[ChangeEntry]:
        entries: List[ChangeEntry] = []
        for line in porcelain.splitlines():
            if not line:
                continue
            status_code = line[:2]
            raw_path = line[3:]

            old_path: Optional[str] = None
            if " -> " in raw_path:
                old_path, new_path = raw_path.split(" -> ", 1)
                path = new_path
            else:
                path = raw_path

            # 去除 git 有时加的引号
            path = path.strip('"')
            if old_path:
                old_path = old_path.strip('"')

            change_type = self._map_change_type(status_code)
            artifact_type = _detect_artifact_type(path)

            entries.append(
                ChangeEntry(
                    path=path,
                    old_path=old_path,
                    change_type=change_type,
                    status_code=status_code,
                    artifact_type=artifact_type,
                    content=None,
                    diff=None,
                )
            )
        return entries

    # ------------------------------------------------------------------
    # 文件内容 & Diff
    # ------------------------------------------------------------------

    def read_file_content(self, rel_path: str) -> Optional[str]:
        """读取仓库内文件的当前完整内容，文件不存在时返回 None。"""
        abs_path = os.path.join(self.directory, rel_path)
        if not os.path.isfile(abs_path):
            return None
        try:
            with open(abs_path, "r", encoding="utf-8", errors="replace") as fh:
                return fh.read()
        except OSError as exc:
            logger.warning("Cannot read %s: %s", abs_path, exc)
            return None

    def get_file_diff(self, rel_path: str, change_type: str) -> str:
        """
        获取文件的变更 diff。

        - modified:  工作区 diff；若工作区无差异则取暂存区 diff
        - created:   以 /dev/null 为基准的全文 diff（展示新增内容）
        - deleted:   以 /dev/null 为目标的删除 diff（展示删除内容）
        - renamed:   依赖 git diff 的重命名跟踪
        """
        rel = rel_path.replace("\\", "/")

        if change_type == "created":
            abs_path = os.path.join(self.directory, rel)
            if os.path.isfile(abs_path):
                diff = self._run_git(
                    ["diff", "--no-index", "--", "/dev/null", rel], check=False
                )
                return diff
            return ""

        if change_type == "deleted":
            # 尝试从暂存区取得已删除文件的 diff
            diff = self._run_git(["diff", "--cached", "--", rel], check=False)
            if diff.strip():
                return diff
            # 从 HEAD 直接 show，构造"全删"diff
            show = self._run_git(["show", f"HEAD:{rel}"], check=False)
            if show.strip():
                lines = "\n".join(f"-{l}" for l in show.splitlines())
                return (
                    f"--- a/{rel}\n"
                    f"+++ /dev/null\n"
                    f"@@ -1,{show.count(chr(10)) + 1} +0,0 @@\n"
                    f"{lines}\n"
                )
            return ""

        # modified / renamed / copied / conflicted
        diff = self._run_git(["diff", "--", rel], check=False)
        if diff.strip():
            return diff
        diff = self._run_git(["diff", "--cached", "--", rel], check=False)
        return diff

    # ------------------------------------------------------------------
    # 公开查询接口
    # ------------------------------------------------------------------

    def get_changes(self, include_content: bool = True, include_diff: bool = True) -> List[ChangeEntry]:
        """
        快照式获取当前所有未提交变更。

        Parameters
        ----------
        include_content:
            是否为 created / modified 文件填充 ``content`` 字段。
        include_diff:
            是否为每条变更填充 ``diff`` 字段。

        Returns
        -------
        List[ChangeEntry]
        """
        entries = self._parse_porcelain(self._status_porcelain())
        for entry in entries:
            if include_content and entry.change_type in {"created", "modified", "renamed"}:
                entry.content = self.read_file_content(entry.path)
            if include_diff:
                entry.diff = self.get_file_diff(entry.path, entry.change_type)
        return entries

    def get_history(self, limit: Optional[int] = None) -> List[ChangeEntry]:
        """返回内存中的历史变更记录（最新在前）。"""
        items = list(reversed(self._history))
        return items[:limit] if limit else items

    def get_committed_log(self, max_count: int = 20) -> List[Dict]:
        """
        返回 Git 提交日志（最新在前），每条包含 hash / author / date / message。
        """
        fmt = "%H\x1f%an\x1f%ai\x1f%s"
        raw = self._run_git(
            ["log", f"--max-count={max_count}", f"--pretty=format:{fmt}"],
            check=False,
        )
        logs = []
        for line in raw.splitlines():
            parts = line.split("\x1f", 3)
            if len(parts) == 4:
                logs.append(
                    {
                        "hash": parts[0],
                        "author": parts[1],
                        "date": parts[2],
                        "message": parts[3],
                    }
                )
        return logs

    def get_file_log(self, rel_path: str, max_count: int = 10) -> List[Dict]:
        """返回某个文件的提交历史。"""
        fmt = "%H\x1f%an\x1f%ai\x1f%s"
        raw = self._run_git(
            ["log", f"--max-count={max_count}", f"--pretty=format:{fmt}", "--follow", "--", rel_path],
            check=False,
        )
        logs = []
        for line in raw.splitlines():
            parts = line.split("\x1f", 3)
            if len(parts) == 4:
                logs.append(
                    {
                        "hash": parts[0],
                        "author": parts[1],
                        "date": parts[2],
                        "message": parts[3],
                    }
                )
        return logs

    # ------------------------------------------------------------------
    # 提交接口
    # ------------------------------------------------------------------

    def add(self, file_pattern: str = ".") -> None:
        """将文件添加到暂存区。"""
        self._run_git(["add", file_pattern])

    def commit(self, message: str, add_all: bool = False) -> str:
        """
        提交变更。

        Parameters
        ----------
        message:
            提交说明。
        add_all:
            提交前是否先执行 ``git add .``。

        Returns
        -------
        str
            git commit 的 stdout 输出。
        """
        if add_all:
            self.add(".")
        return self._run_git(["commit", "-m", message])

    def auto_commit(self, message: Optional[str] = None) -> Optional[str]:
        """
        如果存在未提交变更，自动 add + commit。
        无变更时返回 None。
        """
        if not self._status_porcelain().strip():
            return None
        msg = message or f"chore: auto-commit @ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        return self.commit(msg, add_all=True)

    # ------------------------------------------------------------------
    # 订阅 / 事件分发
    # ------------------------------------------------------------------

    def subscribe(
        self,
        callback: Callable[[List[ChangeEntry]], None],
        *,
        change_types: Optional[Set[str]] = None,
        path_pattern: Optional[str] = None,
        artifact_types: Optional[Set[str]] = None,
    ) -> int:
        """
        注册一个变更事件订阅。

        Parameters
        ----------
        callback:
            收到过滤后变更列表时调用的函数。列表非空时才会调用。
        change_types:
            仅关注的变更类型，e.g. ``{"created", "modified"}``。None 表示全部。
        path_pattern:
            fnmatch 路径通配符，e.g. ``"*.md"``、``"specs/**"``。None 表示全部。
        artifact_types:
            仅关注的制品类型，e.g. ``{"document"}``。None 表示全部。

        Returns
        -------
        int
            订阅 ID，用于 ``unsubscribe``。
        """
        sub = Subscription(
            callback=callback,
            change_types=change_types,
            path_pattern=path_pattern,
            artifact_types=artifact_types,
        )
        with self._sub_lock:
            sid = self._sub_counter
            self._sub_counter += 1
            self._subscriptions[sid] = sub
        logger.debug("Subscription %d registered", sid)
        return sid

    def unsubscribe(self, subscription_id: int) -> bool:
        """取消订阅，返回是否成功找到并移除。"""
        with self._sub_lock:
            removed = self._subscriptions.pop(subscription_id, None)
        return removed is not None

    def _dispatch(self, changes: List[ChangeEntry]) -> None:
        """遍历所有订阅者，按过滤条件分发变更。"""
        with self._sub_lock:
            subs = list(self._subscriptions.values())

        for sub in subs:
            matched = [c for c in changes if sub.matches(c)]
            if matched:
                try:
                    sub.callback(matched)
                except Exception as exc:  # noqa: BLE001
                    logger.exception("Subscription callback raised an exception: %s", exc)

    # ------------------------------------------------------------------
    # 后台监测
    # ------------------------------------------------------------------

    def _watch_loop(self, interval: float) -> None:
        """后台线程主循环：轮询 git status，检测快照差异并分发事件。"""
        logger.info("Watch loop started (interval=%.2fs)", interval)
        last_snapshot = self._status_porcelain()
        # 通知 start_watch：初始快照已就绪，外部调用者可以安全地操作文件了
        self._ready_event.set()

        while not self._stop_event.is_set():
            time.sleep(interval)

            if self._stop_event.is_set():
                break

            try:
                current_snapshot = self._status_porcelain()
            except GitCommandError as exc:
                logger.error("git status failed in watch loop: %s", exc)
                continue

            if current_snapshot == last_snapshot:
                continue

            last_snapshot = current_snapshot
            try:
                changes = self.get_changes(include_content=True, include_diff=True)
            except GitCommandError as exc:
                logger.error("get_changes failed: %s", exc)
                continue

            if not changes:
                continue

            # 写入历史
            self._history.extend(changes)

            # 日志摘要
            for c in changes:
                logger.info(c.summary())

            # 自动提交已检测到的变更，防止下一轮重复检测
            try:
                self.auto_commit()
            except GitCommandError as exc:
                logger.warning("auto_commit in watch loop failed: %s", exc)

            # 分发事件
            self._dispatch(changes)

        logger.info("Watch loop stopped")

    def start_watch(self, interval: float = 1.0) -> None:
        """
        在后台线程中启动目录监测。可多次调用，已在运行时忽略。

        Parameters
        ----------
        interval:
            轮询间隔，单位：秒。
        """
        if self._watch_thread and self._watch_thread.is_alive():
            logger.warning("Watch is already running")
            return

        self._stop_event.clear()
        self._ready_event.clear()
        self._watch_thread = threading.Thread(
            target=self._watch_loop,
            args=(interval,),
            name="GitArtifactPool-Watch",
            daemon=True,  # 主进程退出时自动结束
        )
        self._watch_thread.start()
        # 阻塞直到初始快照已拍，避免调用方在快照前就修改文件导致漏检
        self._ready_event.wait(timeout=10.0)
        logger.info("GitArtifactPool watch started on %s", self.directory)

    def stop_watch(self, timeout: float = 5.0) -> None:
        """
        停止后台监测线程。

        Parameters
        ----------
        timeout:
            等待线程退出的最长时间（秒）。
        """
        self._stop_event.set()
        if self._watch_thread:
            self._watch_thread.join(timeout=timeout)
            if self._watch_thread.is_alive():
                logger.warning("Watch thread did not stop within %.1fs", timeout)
            else:
                logger.info("GitArtifactPool watch stopped")
            self._watch_thread = None

    @property
    def is_watching(self) -> bool:
        """当前是否正在后台监测。"""
        return bool(self._watch_thread and self._watch_thread.is_alive())

    # ------------------------------------------------------------------
    # 便捷上下文管理器
    # ------------------------------------------------------------------

    def __enter__(self) -> "GitArtifactPool":
        self.start_watch()
        return self

    def __exit__(self, *_) -> None:
        self.stop_watch()

    # ------------------------------------------------------------------
    # 调试 / 信息
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        status = "watching" if self.is_watching else "idle"
        return (
            f"GitArtifactPool(directory={self.directory!r}, "
            f"status={status!r}, "
            f"history={len(self._history)}, "
            f"subscriptions={len(self._subscriptions)})"
        )
