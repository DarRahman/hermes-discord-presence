"""Tests for the Hermes Discord Rich Presence plugin.

Covers the two behaviours the plugin owns that are easy to break silently:
resolving the database of the *active profile*, and reading session metadata
out of it.
"""

import os
import sqlite3
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _create_state_db(path, sessions, messages):
    """Create a minimal ``state.db`` holding the columns the plugin reads.

    Args:
        path: Where to create the database.
        sessions: ``(id, title, model, input_tokens, output_tokens)`` tuples.
        messages: ``(session_id, timestamp)`` tuples.
    """
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            title TEXT,
            model TEXT,
            input_tokens INTEGER DEFAULT 0,
            output_tokens INTEGER DEFAULT 0
        );
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            timestamp REAL NOT NULL
        );
        """
    )
    conn.executemany(
        "INSERT INTO sessions (id, title, model, input_tokens, output_tokens) "
        "VALUES (?, ?, ?, ?, ?)",
        sessions,
    )
    conn.executemany(
        "INSERT INTO messages (session_id, role, timestamp) VALUES (?, 'user', ?)",
        messages,
    )
    conn.commit()
    conn.close()
    return str(path)


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    """A fake ``$HOME`` with the profile-related env vars cleared."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData" / "Local"))
    monkeypatch.delenv("HERMES_HOME", raising=False)
    monkeypatch.delenv("HERMES_DATA_DIR_SUFFIX", raising=False)
    return tmp_path


@pytest.fixture
def set_hermes_home(monkeypatch):
    """Point ``HERMES_HOME`` at a directory for the duration of one test."""

    def _set(path):
        monkeypatch.setenv("HERMES_HOME", str(path))
        return str(path)

    return _set


# --------------------------------------------------------------------------
# token formatting
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "count,expected",
    [
        (0, "0"),
        (42, "42"),
        (999, "999"),
        (1000, "1.0k"),
        (1500, "1.5k"),
        (51500, "51.5k"),
        (1_000_000, "1.0M"),
        (4_600_000, "4.6M"),
    ],
)
def test_format_tokens(plugin, count, expected):
    assert plugin._format_tokens(count) == expected


# --------------------------------------------------------------------------
# database path resolution (profile awareness)
# --------------------------------------------------------------------------


def test_database_path_uses_hermes_home(plugin, isolated_home, set_hermes_home):
    """``HERMES_HOME`` points at the state database of the active profile."""
    profile_home = isolated_home / "profiles" / "work"
    profile_home.mkdir(parents=True)
    (profile_home / "state.db").write_bytes(b"")

    set_hermes_home(profile_home)

    assert plugin._get_database_path() == str(profile_home / "state.db")


def test_hermes_home_beats_default_profile_database(plugin, isolated_home, set_hermes_home):
    """A default-profile database must not shadow the active profile's one.

    Regression test: the previous implementation only looked at
    ``~/.hermes/state.db`` and ``~/.config/hermes/state.db``, so every named
    profile silently reported another profile's session.
    """
    default_home = isolated_home / ".hermes"
    default_home.mkdir()
    (default_home / "state.db").write_bytes(b"")

    profile_home = isolated_home / "profiles" / "work"
    profile_home.mkdir(parents=True)
    profile_db = profile_home / "state.db"
    profile_db.write_bytes(b"")

    set_hermes_home(profile_home)

    assert plugin._get_database_path() == str(profile_db)


def test_database_path_never_borrows_another_profile(plugin, isolated_home, set_hermes_home):
    """An empty active profile must not fall back to the default profile's DB."""
    default_home = isolated_home / ".hermes"
    default_home.mkdir()
    (default_home / "state.db").write_bytes(b"")

    profile_home = isolated_home / "profiles" / "fresh"
    profile_home.mkdir(parents=True)

    set_hermes_home(profile_home)

    assert plugin._get_database_path() == str(profile_home / "state.db")


def test_database_path_expands_user_in_hermes_home(plugin, isolated_home, set_hermes_home):
    """``~`` and variables inside ``HERMES_HOME`` are expanded."""
    (isolated_home / "custom-home").mkdir()

    set_hermes_home("~/custom-home")

    assert os.path.normpath(plugin._get_database_path()) == os.path.normpath(
        str(isolated_home / "custom-home" / "state.db")
    )


def test_database_path_defaults_without_hermes_home(plugin, isolated_home):
    expected = (
        isolated_home / "AppData" / "Local" / "hermes" / "state.db"
        if sys.platform == "win32"
        else isolated_home / ".hermes" / "state.db"
    )
    assert plugin._get_database_path() == str(expected)


def test_database_path_honours_data_dir_suffix(plugin, isolated_home, monkeypatch):
    """``HERMES_DATA_DIR_SUFFIX`` shifts the platform default home."""
    monkeypatch.setenv("HERMES_DATA_DIR_SUFFIX", "-dev")

    expected = (
        isolated_home / "AppData" / "Local" / "hermes-dev" / "state.db"
        if sys.platform == "win32"
        else isolated_home / ".hermes-dev" / "state.db"
    )
    assert plugin._get_database_path() == str(expected)


def test_database_path_falls_back_to_legacy_config_location(plugin, isolated_home):
    """``~/.config/hermes/state.db`` still works when the default one is absent."""
    legacy = isolated_home / ".config" / "hermes"
    legacy.mkdir(parents=True)
    (legacy / "state.db").write_bytes(b"")

    assert plugin._get_database_path() == str(legacy / "state.db")


def test_database_path_windows_uses_local_appdata(plugin, isolated_home, monkeypatch):
    """On Windows the default home is ``%LOCALAPPDATA%\\hermes``."""
    local_appdata = isolated_home / "AppData" / "Local"
    local_appdata.mkdir(parents=True)
    monkeypatch.setenv("LOCALAPPDATA", str(local_appdata))
    monkeypatch.setattr(sys, "platform", "win32")

    expected = os.path.join(str(local_appdata), "hermes", "state.db")
    assert plugin._get_database_path() == expected


# --------------------------------------------------------------------------
# session metadata lookup
# --------------------------------------------------------------------------


def test_session_details_reads_most_recent_session(plugin, tmp_path):
    db = _create_state_db(
        tmp_path / "state.db",
        sessions=[
            ("s_old", "Old work", "model-a", 100, 50),
            ("s_new", "New work", "model-b", 2000, 500),
        ],
        messages=[("s_old", 1000.0), ("s_new", 2000.0)],
    )

    details = plugin.DiscordRPCPlugin().get_active_session_details(db_path=db)

    assert details == {"title": "New work", "model": "model-b", "total_tokens": 2500}


def test_session_details_ignores_cron_sessions(plugin, tmp_path):
    """Cron runs must not take over the presence."""
    db = _create_state_db(
        tmp_path / "state.db",
        sessions=[
            ("cron_20260101", "Nightly job", "model-c", 10, 10),
            ("s_interactive", "Interactive", "model-a", 10, 20),
        ],
        messages=[("cron_20260101", 5000.0), ("s_interactive", 1000.0)],
    )

    details = plugin.DiscordRPCPlugin().get_active_session_details(db_path=db)

    assert details == {"title": "Interactive", "model": "model-a", "total_tokens": 30}


def test_session_details_skips_sessions_without_messages(plugin, tmp_path):
    db = _create_state_db(
        tmp_path / "state.db",
        sessions=[
            ("s_empty", "No messages yet", "model-x", 0, 0),
            ("s_active", "Has messages", "model-a", 5, 5),
        ],
        messages=[("s_active", 1.0)],
    )

    details = plugin.DiscordRPCPlugin().get_active_session_details(db_path=db)

    assert details["title"] == "Has messages"


def test_session_details_defaults_when_database_is_missing(plugin, tmp_path):
    details = plugin.DiscordRPCPlugin().get_active_session_details(
        db_path=str(tmp_path / "does-not-exist.db")
    )

    assert details == {
        "title": "Active Workspace",
        "model": "Hermes Agent",
        "total_tokens": 0,
    }


def test_session_details_follow_the_active_profile(plugin, isolated_home, set_hermes_home):
    """End-to-end: ``HERMES_HOME`` decides which database is read."""
    default_home = isolated_home / ".hermes"
    default_home.mkdir()
    _create_state_db(
        default_home / "state.db",
        sessions=[("s_default", "Default profile", "model-default", 1, 1)],
        messages=[("s_default", 9999.0)],
    )

    profile_home = isolated_home / "profiles" / "work"
    profile_home.mkdir(parents=True)
    _create_state_db(
        profile_home / "state.db",
        sessions=[("s_profile", "Profile session", "model-profile", 10, 20)],
        messages=[("s_profile", 1.0)],
    )

    set_hermes_home(profile_home)

    details = plugin.DiscordRPCPlugin().get_active_session_details()

    assert details == {"title": "Profile session", "model": "model-profile", "total_tokens": 30}


# --------------------------------------------------------------------------
# lifecycle contract
# --------------------------------------------------------------------------


class _FakeCtx:
    """Minimal stand-in for the ``PluginContext`` handed to ``register()``."""

    def __init__(self):
        self.hooks = {}

    def register_hook(self, name, callback):
        self.hooks.setdefault(name, []).append(callback)


def test_register_wires_the_declared_hooks(plugin, monkeypatch):
    """``register()`` subscribes exactly the hooks ``plugin.yaml`` advertises."""
    monkeypatch.setattr(plugin._plugin_instance, "update_presence", lambda: None)

    ctx = _FakeCtx()
    plugin.register(ctx)

    assert set(ctx.hooks) == {
        "pre_llm_call",
        "pre_tool_call",
        "post_tool_call",
        "on_session_end",
        "on_session_finalize",
    }

    manifest = (PLUGIN_ROOT / "plugin.yaml").read_text(encoding="utf-8")
    for hook in ctx.hooks:
        assert f"- {hook}" in manifest, f"{hook} missing from plugin.yaml provides_hooks"


def test_connect_degrades_gracefully_without_pypresence(plugin, monkeypatch):
    """A missing ``pypresence`` must disable presence, not raise."""
    monkeypatch.setitem(sys.modules, "pypresence", None)

    instance = plugin.DiscordRPCPlugin()

    assert instance.connect() is False
    assert instance.is_connected is False
