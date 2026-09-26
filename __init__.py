"""Discord Rich Presence (RPC) plugin for Hermes Agent."""

import os
import sqlite3
import sys
import threading
import time
from typing import Any, Dict, List, Optional

CLIENT_ID = "1530932637546451074"

TOOL_DISPLAY_MAP = {
    "terminal": "Running Terminal Command",
    "execute_code": "Running Python Kernel",
    "read_file": "Reading File",
    "write_file": "Writing File",
    "patch": "Editing Code",
    "search_files": "Searching Project Files",
    "web_search": "Browsing Web",
    "web_extract": "Extracting Web Content",
    "vision_analyze": "Analyzing Image",
    "delegate_task": "Coordinating Subagents",
    "clarify": "Awaiting User Input",
    "todo_list": "Managing Tasks",
    "cronjob_manage": "Scheduling Task",
}


def _safe_truncate(text: Optional[str], max_len: int = 120) -> Optional[str]:
    """Truncate text cleanly with ellipsis if length exceeds max_len."""
    if not text:
        return text
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def _format_tool_activity(tool_name: str) -> str:
    """Map tool name to human-readable activity description."""
    if not tool_name:
        return "Executing Tool"
    if tool_name in TOOL_DISPLAY_MAP:
        return TOOL_DISPLAY_MAP[tool_name]
    if tool_name.startswith("mcp__"):
        parts = tool_name.split("__")
        return f"Running MCP Tool ({parts[1]})" if len(parts) > 1 else "Running MCP Tool"
    clean_name = tool_name.replace("_", " ").title()
    return f"Running {clean_name}"


def _expand_path(path: str) -> str:
    """Expand ~ and environment-variable syntax in a path string."""
    return os.path.normpath(os.path.expanduser(os.path.expandvars(path)))


def _default_hermes_home() -> str:
    """Platform default Hermes home, mirroring Hermes resolution."""
    suffix = os.environ.get("HERMES_DATA_DIR_SUFFIX", "")
    if sys.platform == "win32":
        local_appdata = os.environ.get("LOCALAPPDATA", "").strip()
        base = local_appdata or os.path.join(os.path.expanduser("~"), "AppData", "Local")
        return os.path.join(base, "hermes" + suffix)
    return os.path.join(os.path.expanduser("~"), ".hermes" + suffix)


def _candidate_database_paths() -> List[str]:
    """Ordered candidate paths for the Hermes SQLite state database."""
    hermes_home = os.environ.get("HERMES_HOME", "").strip()
    if hermes_home:
        return [os.path.join(_expand_path(hermes_home), "state.db")]

    return [
        os.path.join(_default_hermes_home(), "state.db"),
        os.path.join(os.path.expanduser("~"), ".config", "hermes", "state.db"),
    ]


def _get_database_path() -> str:
    """Resolve the state database of the active profile."""
    candidates = _candidate_database_paths()
    return next((path for path in candidates if os.path.exists(path)), candidates[0])


_CONFIG_CACHE: Dict[str, Any] = {}
_CONFIG_MTIME: float = 0.0


def _load_config() -> Dict[str, Any]:
    """Load configuration from plugin directory with automatic reload on change."""
    global _CONFIG_CACHE, _CONFIG_MTIME
    cfg_path = os.path.join(os.path.dirname(__file__), "config.yaml")
    if os.path.exists(cfg_path):
        try:
            mtime = os.path.getmtime(cfg_path)
            if mtime != _CONFIG_MTIME:
                import yaml

                with open(cfg_path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                    if isinstance(data, dict):
                        _CONFIG_CACHE = data
                        _CONFIG_MTIME = mtime
            return _CONFIG_CACHE
        except Exception:
            pass
    return _CONFIG_CACHE


def _ensure_xdg_runtime_dir() -> None:
    """Ensure XDG_RUNTIME_DIR is set for Discord IPC on Linux desktop/systemd."""
    if sys.platform not in ("linux", "darwin") or os.environ.get("XDG_RUNTIME_DIR"):
        return

    runtime = f"/run/user/{os.getuid()}"
    if os.path.isdir(runtime):
        os.environ["XDG_RUNTIME_DIR"] = runtime
        return

    try:
        import psutil

        for proc in psutil.process_iter(["name"]):
            name = (proc.info.get("name") or "").lower()
            if "discord" not in name:
                continue
            try:
                env = proc.environ()
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                continue
            runtime = env.get("XDG_RUNTIME_DIR")
            if runtime and os.path.isdir(runtime):
                os.environ["XDG_RUNTIME_DIR"] = runtime
                return
    except Exception:
        pass


def _format_tokens(count: int) -> str:
    """Format token count into compact human-readable string."""
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M"
    if count >= 1_000:
        return f"{count / 1_000:.1f}k"
    return str(count)


class DiscordRPCPlugin:
    """Singleton managing Discord RPC connection and state lifecycle."""

    def __init__(self):
        self.config = _load_config()
        self.client_id = str(self.config.get("discord_client_id") or CLIENT_ID)
        self.update_interval = float(self.config.get("update_interval") or 3.0)
        self.hold_duration = float(self.config.get("hold_duration") or 5.0)

        presence_cfg = self.config.get("presence") or {}
        self.large_image = str(presence_cfg.get("large_image") or "hermes_logo")
        self.large_text_template = str(presence_cfg.get("large_text") or "Hermes Agent")

        self.rpc: Optional[Any] = None
        self.is_connected = False
        self.start_time = time.time()
        self.current_status = "Active"
        self.status_hold_until = 0.0
        self.last_state_key: Optional[str] = None
        self._lock = threading.Lock()

    def connect(self) -> bool:
        """Connect to local Discord IPC socket."""
        if self.is_connected:
            return True
        try:
            from pypresence import Presence
        except ImportError:
            self.is_connected = False
            return False

        _ensure_xdg_runtime_dir()
        for pipe in (None, *range(10)):
            try:
                self.rpc = Presence(self.client_id, pipe=pipe)
                self.rpc.connect()
                self.is_connected = True
                self.start_time = time.time()
                return True
            except Exception:
                self.rpc = None
        self.is_connected = False
        return False

    def disconnect(self):
        """Cleanly disconnect from Discord RPC."""
        with self._lock:
            if self.rpc and self.is_connected:
                try:
                    self.rpc.close()
                except Exception:
                    pass
            self.is_connected = False
            self.last_state_key = None

    def get_active_session_details(self, db_path: Optional[str] = None) -> Dict[str, Any]:
        """Query active session metadata from SQLite state database."""
        db_path = db_path or _get_database_path()
        details = {
            "title": "Active Workspace",
            "model": "Hermes Agent",
            "total_tokens": 0,
        }

        if not os.path.exists(db_path):
            return details

        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            cursor = conn.cursor()
            try:
                row = cursor.execute(
                    "SELECT s.title, s.model, "
                    "       COALESCE(s.input_tokens, 0) + COALESCE(s.output_tokens, 0) + COALESCE(s.reasoning_tokens, 0) AS total_tokens "
                    "FROM messages m "
                    "JOIN sessions s ON m.session_id = s.id "
                    "WHERE s.id NOT LIKE 'cron%' AND s.title IS NOT NULL AND s.model IS NOT NULL "
                    "GROUP BY s.id "
                    "ORDER BY MAX(m.timestamp) DESC LIMIT 1"
                ).fetchone()
            except sqlite3.OperationalError:
                row = cursor.execute(
                    "SELECT s.title, s.model, "
                    "       COALESCE(s.input_tokens, 0) + COALESCE(s.output_tokens, 0) AS total_tokens "
                    "FROM messages m "
                    "JOIN sessions s ON m.session_id = s.id "
                    "WHERE s.id NOT LIKE 'cron%' AND s.title IS NOT NULL AND s.model IS NOT NULL "
                    "GROUP BY s.id "
                    "ORDER BY MAX(m.timestamp) DESC LIMIT 1"
                ).fetchone()

            if row:
                if row[0]:
                    details["title"] = str(row[0])
                if row[1]:
                    details["model"] = str(row[1])
                if row[2]:
                    details["total_tokens"] = int(row[2])

            conn.close()
        except Exception:
            pass

        return details

    def set_status(self, status: str, hold: bool = False):
        """Update activity status with optional minimum hold duration."""
        with self._lock:
            now = time.time()
            if not hold and now < self.status_hold_until:
                return
            self.current_status = status
            if hold:
                self.status_hold_until = now + self.hold_duration
        self.update_presence()

    def update_presence(self):
        """Push latest state and presence payload to Discord RPC."""
        with self._lock:
            self.config = _load_config()
            now = time.time()
            if now >= self.status_hold_until and self.current_status not in ("Active", "Idle"):
                self.current_status = "Active"

            if not self.connect():
                return

            try:
                data = self.get_active_session_details()
                title = data["title"]
                raw_model = data["model"]
                tokens_str = _format_tokens(data["total_tokens"])

                privacy = self.config.get("privacy") or {}
                stealth = bool(privacy.get("stealth_mode", False))
                hide_model = bool(privacy.get("hide_model", False))
                hide_tokens = bool(privacy.get("hide_tokens", False))
                hide_tool = bool(privacy.get("hide_tool_status", False))
                title_mode = str(privacy.get("session_title_mode", "full"))

                if stealth:
                    details_str = None
                    state_str = None
                else:
                    is_active_tool = (self.current_status not in ("Active", "Idle")) and not hide_tool
                    if is_active_tool:
                        details_str = f"[{self.current_status}]"
                        if title_mode == "full" and title:
                            details_str = f"[{self.current_status}] {title}"
                    else:
                        if title_mode == "hidden":
                            details_str = None
                        elif title_mode == "generic":
                            details_str = "Active Session"
                        else:
                            details_str = f"Session: {title}" if title else "Active Session"

                    state_parts = []
                    if not hide_model:
                        state_parts.append(raw_model)
                    if not hide_tokens and data["total_tokens"] > 0:
                        state_parts.append(f"{tokens_str} tokens")
                    state_str = " • ".join(state_parts) if state_parts else None

                details_str = _safe_truncate(details_str, 120)
                state_str = _safe_truncate(state_str, 120)

                large_text = f"{self.large_text_template} — {self.current_status}"
                large_text = _safe_truncate(large_text, 120)

                state_key = f"{details_str}|{state_str}|{large_text}|{self.current_status}"
                if state_key != self.last_state_key:
                    self.last_state_key = state_key
                    self.rpc.update(
                        details=details_str,
                        state=state_str,
                        large_image=self.large_image,
                        large_text=large_text,
                        start=int(self.start_time),
                    )
            except Exception:
                self.is_connected = False


_plugin_instance = DiscordRPCPlugin()


def _on_pre_llm(*args, **kwargs):
    _plugin_instance.set_status("Thinking", hold=True)


def _on_pre_tool(tool_name: str = "", **kwargs):
    _plugin_instance.set_status(_format_tool_activity(tool_name), hold=True)


def _on_post_tool(*args, **kwargs):
    _plugin_instance.set_status("Processing", hold=False)


def _on_session_end(*args, **kwargs):
    _plugin_instance.set_status("Active", hold=False)


def _on_session_finalize(*args, **kwargs):
    _plugin_instance.disconnect()


def register(ctx):
    """Entry point invoked by Hermes plugin loader on launch."""
    ctx.register_hook("pre_llm_call", _on_pre_llm)
    ctx.register_hook("pre_tool_call", _on_pre_tool)
    ctx.register_hook("post_tool_call", _on_post_tool)
    ctx.register_hook("on_session_end", _on_session_end)
    ctx.register_hook("on_session_finalize", _on_session_finalize)

    threading.Thread(target=_plugin_instance.update_presence, daemon=True).start()

    def _loop():
        while True:
            time.sleep(_plugin_instance.update_interval)
            _plugin_instance.update_presence()

    threading.Thread(target=_loop, daemon=True).start()
