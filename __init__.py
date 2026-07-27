"""
Hermes Agent - Discord Rich Presence (RPC) Plugin
Feature Branch: feature/enhanced-presence

Provides clean, polished, real-time Discord Rich Presence with session titles,
model name, token counters, workspace/git context, and live status activity.

Author: Badar Rahman
License: MIT
"""

import os
import sqlite3
import subprocess
import threading
import time
from typing import Optional, Tuple, Dict, Any
from pypresence import Presence

CLIENT_ID = "1530932637546451074"


def _get_database_path() -> str:
    """Resolve platform-independent path for Hermes SQLite state database."""
    home = os.path.expanduser("~")
    candidates = [
        os.path.join(home, "AppData", "Local", "hermes", "state.db"),
        os.path.join(home, ".hermes", "state.db"),
        os.path.join(home, ".config", "hermes", "state.db"),
    ]
    return next((path for path in candidates if os.path.exists(path)), candidates[0])


def _format_tokens(count: int) -> str:
    """Format token count into readable string (e.g. 51.5k, 4.6M)."""
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M"
    elif count >= 1_000:
        return f"{count / 1_000:.1f}k"
    return str(count)


def _get_git_branch_fallback(folder_path: str) -> Optional[str]:
    """Fallback git branch lookup using git CLI if DB field is null."""
    if not folder_path or not os.path.exists(folder_path):
        return None
    try:
        res = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=folder_path,
            capture_output=True,
            text=True,
            timeout=1
        )
        if res.returncode == 0:
            branch = res.stdout.strip()
            return branch if branch and branch != "HEAD" else None
    except Exception:
        pass
    return None


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
        try:
            self.rpc = Presence(CLIENT_ID)
            self.rpc.connect()
            self.is_connected = True
            self.start_time = time.time()
            return True
        except Exception:
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
        """Query active session metadata (title, model, project, branch, tokens) from SQLite."""
        db_path = _get_database_path()
        details = {
            "title": "Active Workspace",
            "model": "Hermes Agent",
            "project": "",
            "branch": "",
            "total_tokens": 0
        }

        if os.path.exists(db_path):
            try:
                conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
                cursor = conn.cursor()
                row = cursor.execute(
                    "SELECT s.title, s.model, s.cwd, s.git_branch, "
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
                    
                    cwd_path = str(row[2]) if row[2] else os.getcwd()
                    details["project"] = os.path.basename(cwd_path.rstrip(r"\/"))
                    
                    # Read git branch from DB or via git CLI fallback
                    branch = str(row[3]) if row[3] else _get_git_branch_fallback(cwd_path)
                    if branch:
                        details["branch"] = branch
                        
                    if row[4]: details["total_tokens"] = int(row[4])

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
                model = data["model"]
                tokens_str = _format_tokens(data["total_tokens"])
                
                # Clean Model Name (strip prefixes like 'ag/' if long)
                clean_model = model.split("/")[-1] if "/" in model else model
                
                # Line 1 (Details): "Session: <Title>" or "[Thinking...] Session: <Title>"
                if self.current_status != "Active" and self.current_status != "Idle":
                    details_str = f"[{self.current_status}] {title}"
                else:
                    details_str = f"Session: {title}"

                # Line 2 (State): "<Model> (<Tokens>) | <Project> (<Branch>)"
                state_parts = [f"{clean_model} ({tokens_str})"]
                
                if data["project"] and data["project"].lower() != os.path.basename(os.path.expanduser("~")).lower():
                    proj_info = data["project"]
                    if data["branch"]:
                        proj_info += f" ({data['branch']})"
                    state_parts.append(proj_info)

                state_str = " | ".join(state_parts)

                state_key = f"{details_str}|{state_str}|{self.current_status}"
                
                if state_key != self.last_state_key:
                    self.last_state_key = state_key
                    self.rpc.update(
                        details=details_str,
                        state=state_str,
                        large_image="hermes_logo",
                        large_text=f"Hermes Agent — Status: {self.current_status}",
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


def register(ctx):
    """Plugin initialization entry point called by Hermes plugin loader on launch."""
    ctx.register_hook("pre_llm_call", _on_pre_llm)
    ctx.register_hook("pre_tool_call", _on_pre_tool)
    ctx.register_hook("post_tool_call", _on_post_tool)
    ctx.register_hook("on_session_end", _on_session_end)
    
    # Trigger initial update on application startup
    threading.Thread(target=_plugin_instance.update_presence, daemon=True).start()

    # Continuous background loop (updates every 3 seconds)
    def _loop():
        while True:
            time.sleep(3)
            _plugin_instance.update_presence()
            
    threading.Thread(target=_loop, daemon=True).start()
