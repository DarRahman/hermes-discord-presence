"""
Hermes Agent - Discord Rich Presence (RPC) Plugin
Feature Branch: feature/enhanced-presence

Provides clean, polished, real-time Discord Rich Presence with session titles,
model name, token counters, and live status activity.

Author: Badar Rahman
License: MIT
"""

import os
import sqlite3
import sys
import threading
import time
from typing import Optional, Dict, Any
from pypresence import Presence

CLIENT_ID = "1530932637546451074"


def _get_database_path() -> str:
    """Resolve platform-independent path for Hermes SQLite state database."""
    home = os.path.expanduser("~")
    if sys.platform == "win32":
        candidates = [os.path.join(home, "AppData", "Local", "hermes", "state.db")]
    else:
        candidates = [
            os.path.join(home, ".hermes", "state.db"),
            os.path.join(home, ".config", "hermes", "state.db"),
        ]
    return next((path for path in candidates if os.path.exists(path)), candidates[0])


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
    """Format token count into readable string (e.g. 51.5k, 4.6M)."""
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M"
    elif count >= 1_000:
        return f"{count / 1_000:.1f}k"
    return str(count)


class DiscordRPCPlugin:
    """Singleton plugin class managing Discord RPC connection and state updates."""

    def __init__(self):
        self.rpc: Optional[Presence] = None
        self.is_connected = False
        self.start_time = time.time()
        self.current_status = "Active"
        self.last_state_key: Optional[str] = None
        self._lock = threading.Lock()

    def connect(self) -> bool:
        """Connect to local Discord IPC socket."""
        if self.is_connected:
            return True
        _ensure_xdg_runtime_dir()
        for pipe in (None, *range(10)):
            try:
                self.rpc = Presence(CLIENT_ID, pipe=pipe)
                self.rpc.connect()
                self.is_connected = True
                self.start_time = time.time()
                return True
            except Exception:
                self.rpc = None
        self.is_connected = False
        return False

    def disconnect(self):
        """Disconnect cleanly from Discord RPC."""
        if self.rpc and self.is_connected:
            try:
                self.rpc.close()
            except Exception:
                pass
        self.is_connected = False
        self.last_state_key = None

    def get_active_session_details(self) -> Dict[str, Any]:
        """Query active session metadata (title, model, tokens) from SQLite."""
        db_path = _get_database_path()
        details = {
            "title": "Active Workspace",
            "model": "Hermes Agent",
            "total_tokens": 0
        }

        if os.path.exists(db_path):
            try:
                conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
                cursor = conn.cursor()
                row = cursor.execute(
                    "SELECT s.title, s.model, "
                    "       COALESCE(s.input_tokens, 0) + COALESCE(s.output_tokens, 0) AS total_tokens "
                    "FROM messages m "
                    "JOIN sessions s ON m.session_id = s.id "
                    "WHERE s.id NOT LIKE 'cron%' "
                    "GROUP BY m.session_id "
                    "ORDER BY MAX(m.timestamp) DESC LIMIT 1"
                ).fetchone()
                
                if row:
                    if row[0]: details["title"] = str(row[0])
                    if row[1]: details["model"] = str(row[1])
                    if row[2]: details["total_tokens"] = int(row[2])

                conn.close()
            except Exception:
                pass

        return details

    def set_status(self, status: str):
        """Update current agent activity status."""
        self.current_status = status
        self.update_presence()

    def update_presence(self):
        """Thread-safe update handler pushed to Discord RPC."""
        with self._lock:
            if not self.connect():
                return
            try:
                data = self.get_active_session_details()
                title = data["title"]
                raw_model = data["model"]
                tokens_str = _format_tokens(data["total_tokens"])
                
                # Line 1 (Details): Clean bracketed status
                if self.current_status != "Active" and self.current_status != "Idle":
                    details_str = f"[{self.current_status}] {title}"
                else:
                    details_str = f"Session: {title}"

                # Line 2 (State): Model • Tokens
                state_parts = [raw_model]
                if data["total_tokens"] > 0:
                    state_parts.append(f"{tokens_str} tokens")

                state_str = " • ".join(state_parts)
                # ponytail: clean layout without emojis or path lookup; upgrade path: re-add git context if daemon process exposes git root

                state_key = f"{details_str}|{state_str}|{self.current_status}"
                
                if state_key != self.last_state_key:
                    self.last_state_key = state_key
                    self.rpc.update(
                        details=details_str,
                        state=state_str,
                        large_image="hermes_logo",
                        large_text=f"Hermes Agent — {self.current_status}",
                        start=int(self.start_time)
                    )
            except Exception:
                self.is_connected = False


_plugin_instance = DiscordRPCPlugin()


def _on_pre_llm(*args, **kwargs):
    """Hook: Before LLM call."""
    _plugin_instance.set_status("Thinking")


def _on_pre_tool(tool_name: str = "", **kwargs):
    """Hook: Before tool execution."""
    act = f"Running {tool_name}" if tool_name else "Executing Tool"
    _plugin_instance.set_status(act)


def _on_post_tool(*args, **kwargs):
    """Hook: After tool execution."""
    _plugin_instance.set_status("Processing")


def _on_session_end(*args, **kwargs):
    """Hook: Turn / Session completed."""
    _plugin_instance.set_status("Active")


def _on_session_finalize(*args, **kwargs):
    """Hook: Hermes session teardown — disconnect Discord RPC cleanly."""
    _plugin_instance.disconnect()


def register(ctx):
    """Plugin initialization entry point called by Hermes plugin loader on launch."""
    ctx.register_hook("pre_llm_call", _on_pre_llm)
    ctx.register_hook("pre_tool_call", _on_pre_tool)
    ctx.register_hook("post_tool_call", _on_post_tool)
    ctx.register_hook("on_session_end", _on_session_end)
    ctx.register_hook("on_session_finalize", _on_session_finalize)
    
    # Trigger initial update on application startup
    threading.Thread(target=_plugin_instance.update_presence, daemon=True).start()

    # Continuous background loop (updates every 3 seconds)
    def _loop():
        while True:
            time.sleep(3)
            _plugin_instance.update_presence()
            
    threading.Thread(target=_loop, daemon=True).start()
