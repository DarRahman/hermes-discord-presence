"""
Hermes Agent - Discord Rich Presence (RPC) Plugin

A native, zero-dependency plugin for Hermes Agent. Automatically syncs active session
metadata and AI model information to Discord Rich Presence via Hermes plugin hooks.

Author: Badar Rahman
License: MIT
"""

import os
import sqlite3
import threading
import time
from typing import Optional, Tuple
from pypresence import Presence

# Default Discord Application Client ID registered for Hermes Agent
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


class DiscordRPCPlugin:
    """Singleton plugin class managing Discord RPC connection and state updates."""

    def __init__(self):
        self.rpc: Optional[Presence] = None
        self.is_connected = False
        self.start_time = time.time()
        self.last_title: Optional[str] = None
        self.last_model: Optional[str] = None
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
        self.last_title = None
        self.last_model = None

    def get_active_session_info(self) -> Tuple[str, str]:
        """Query active session title and model from SQLite state database."""
        db_path = _get_database_path()
        title = "Active Workspace"
        model = "Hermes Agent"

        if os.path.exists(db_path):
            try:
                conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
                cursor = conn.cursor()
                row = cursor.execute(
                    "SELECT s.title, s.model "
                    "FROM messages m "
                    "JOIN sessions s ON m.session_id = s.id "
                    "WHERE s.id NOT LIKE 'cron%' "
                    "GROUP BY m.session_id "
                    "ORDER BY MAX(m.timestamp) DESC LIMIT 1"
                ).fetchone()
                
                if row:
                    if row[0]:
                        title = str(row[0])
                    if row[1]:
                        model = str(row[1])

                conn.close()
            except Exception:
                pass

        return title, model

    def update_presence(self):
        """Thread-safe update handler pushed to Discord RPC."""
        with self._lock:
            if not self.connect():
                return
            try:
                title, model = self.get_active_session_info()
                if title != self.last_title or model != self.last_model:
                    self.last_title = title
                    self.last_model = model
                    self.rpc.update(
                        details=f"Session: {title}",
                        state=f"Model: {model}",
                        large_image="hermes_logo",
                        large_text="Hermes Agent",
                        start=int(self.start_time),
                    )
            except Exception:
                self.is_connected = False


_plugin_instance = DiscordRPCPlugin()


def _on_activity(**kwargs):
    """Event handler triggered on Hermes lifecycle hooks."""
    threading.Thread(target=_plugin_instance.update_presence, daemon=True).start()


def register(ctx):
    """Plugin initialization entry point called by Hermes plugin loader on launch."""
    ctx.register_hook("pre_llm_call", _on_activity)
    ctx.register_hook("post_tool_call", _on_activity)
    ctx.register_hook("on_session_end", _on_activity)
    
    # Trigger initial update on application startup
    threading.Thread(target=_plugin_instance.update_presence, daemon=True).start()

    # Continuous background loop (updates every 3 seconds)
    def _loop():
        while True:
            time.sleep(3)
            _plugin_instance.update_presence()
            
    threading.Thread(target=_loop, daemon=True).start()
