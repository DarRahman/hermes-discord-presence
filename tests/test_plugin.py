"""Tests for the Hermes Discord Rich Presence plugin.

Covers the two behaviours the plugin owns that are easy to break silently:
resolving the database of the *active profile*, and reading session metadata
out of it.
"""

import os
import sqlite3
import sys
import time
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


# --------------------------------------------------------------------------
# v1.2.0 feature tests
# --------------------------------------------------------------------------


def test_format_tool_activity(plugin):
    assert plugin._format_tool_activity("terminal") == "Running Terminal Command"
    assert plugin._format_tool_activity("execute_code") == "Running Python Kernel"
    assert plugin._format_tool_activity("mcp__github__create_issue") == "Running MCP Tool (github)"
    assert plugin._format_tool_activity("custom_action") == "Running Custom Action"
    assert plugin._format_tool_activity("") == "Executing Tool"


def test_safe_truncate(plugin):
    assert plugin._safe_truncate(None) is None
    assert plugin._safe_truncate("short text", 20) == "short text"
    assert plugin._safe_truncate("hello world", 8) == "hello..."
    assert len(plugin._safe_truncate("A" * 150, 120)) == 120


def test_hold_timer_preserves_status(plugin):
    """Hold timer prevents rapid lifecycle events from clearing active tool state."""
    instance = plugin.DiscordRPCPlugin()
    instance.set_status("Running Terminal Command", hold=True)
    assert instance.current_status == "Running Terminal Command"

    instance.set_status("Processing", hold=False)
    assert instance.current_status == "Running Terminal Command"

    instance.set_status("Active", hold=False)
    assert instance.current_status == "Running Terminal Command"

    instance.set_status("Running Python Kernel", hold=True)
    assert instance.current_status == "Running Python Kernel"


# --------------------------------------------------------------------------
# privacy: the activity label must not leak through the large-text line
# --------------------------------------------------------------------------


class _CapturingRPC:
    """Stand-in for the pypresence client that records published payloads."""

    def __init__(self):
        self.updates = []

    def update(self, **kwargs):
        self.updates.append(kwargs)

    def close(self):
        pass


def _publish_status(plugin, monkeypatch, status, **privacy):
    """Publish ``status`` and return the payload Discord would have received.

    The plugin is put in a connected state with a captured client, so the test
    exercises the real payload assembly in :meth:`DiscordRPCPlugin.update_presence`
    without needing a live Discord process.
    """
    config = {
        "presence": {"large_image": "hermes_logo", "large_text": "Hermes Agent"},
        "privacy": {
            "session_title_mode": "full",
            "hide_model": False,
            "hide_tokens": False,
            "hide_tool_status": False,
            "stealth_mode": False,
        },
    }
    config["privacy"].update(privacy)

    monkeypatch.setattr(plugin, "_load_config", lambda: config)
    monkeypatch.setattr(
        plugin.DiscordRPCPlugin,
        "get_active_session_details",
        lambda self: {"title": "Secret Project", "model": "model-x", "total_tokens": 4242},
    )

    instance = plugin.DiscordRPCPlugin()
    instance.rpc = _CapturingRPC()
    instance.is_connected = True
    instance.set_status(status, hold=True)
    return instance.rpc.updates[-1]


@pytest.mark.parametrize("status", ["Thinking", "Running Terminal Command", "Processing", "Active"])
def test_stealth_mode_withholds_the_activity_label(plugin, monkeypatch, status):
    """``stealth_mode`` is documented as "app name and elapsed time only".

    Regression test: the activity label used to reach Discord through the
    large-text line (``"Hermes Agent — Running Terminal Command"``) even though
    the details and state lines were blanked, so opting into stealth mode still
    published what the agent was doing.
    """
    payload = _publish_status(plugin, monkeypatch, status, stealth_mode=True)

    assert payload["details"] is None
    assert payload["state"] is None
    assert payload["large_text"] == "Hermes Agent"


def test_hide_tool_status_suppresses_the_label_everywhere(plugin, monkeypatch):
    """``hide_tool_status`` is documented as suppressing ``[Running ...]`` tags.

    The details line honoured the toggle, but the same label kept appearing in
    the large-text line, so the tool name stayed visible to other Discord users.
    """
    payload = _publish_status(plugin, monkeypatch, "Running Terminal Command", hide_tool_status=True)

    assert "Running Terminal Command" not in (payload["details"] or "")
    assert "Running Terminal Command" not in payload["large_text"]


def test_hide_tool_status_keeps_a_neutral_status(plugin, monkeypatch):
    """``hide_tool_status`` covers ``[Running ...]`` tags, not lifecycle words.

    "Active" and "Thinking" name no tool, so they stay on the large-text line.
    """
    for status in ("Active", "Thinking", "Processing"):
        payload = _publish_status(plugin, monkeypatch, status, hide_tool_status=True)
        assert payload["large_text"] == f"Hermes Agent — {status}"


def test_default_config_still_shows_the_activity_label(plugin, monkeypatch):
    """Users who have not opted into privacy keep the documented output."""
    payload = _publish_status(plugin, monkeypatch, "Running Terminal Command")

    assert payload["details"] == "[Running Terminal Command] Secret Project"
    assert payload["large_text"] == "Hermes Agent — Running Terminal Command"
    assert payload["state"] == "model-x • 4.2k tokens"


def test_unconfigured_session_title_mode_defaults_to_generic(plugin, monkeypatch):
    """When privacy config omits session_title_mode, it falls back to generic ('Active Session')."""
    config = {
        "presence": {"large_image": "hermes_logo", "large_text": "Hermes Agent"},
        "privacy": {},
    }
    monkeypatch.setattr(plugin, "_load_config", lambda: config)
    monkeypatch.setattr(
        plugin.DiscordRPCPlugin,
        "get_active_session_details",
        lambda self: {"title": "Confidential Restructure", "model": "model-x", "total_tokens": 4242},
    )

    instance = plugin.DiscordRPCPlugin()
    instance.rpc = _CapturingRPC()
    instance.is_connected = True
    instance.set_status("Active", hold=True)
    payload = instance.rpc.updates[-1]

    assert payload["details"] == "Active Session"
    assert "Confidential Restructure" not in (payload["details"] or "")



# --------------------------------------------------------------------------
# multi-terminal ownership of the single Discord presence slot
# --------------------------------------------------------------------------


def _connected_instance(plugin, monkeypatch, label, runtime_dir):
    """Build a connected instance whose rendered payload is distinct per label.

    Each instance reads a different session, so its payload differs from the other
    terminal's. That matters: the bug under test is an instance staying silent
    because *its own* payload is unchanged while another terminal owns the slot.
    """
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime_dir))
    monkeypatch.setattr(
        plugin, "_load_config",
        lambda: {
            "presence": {"large_image": "hermes_logo", "large_text": "Hermes Agent"},
            "privacy": {"session_title_mode": "hidden", "hide_model": False,
                        "hide_tokens": False, "hide_tool_status": True,
                        "stealth_mode": False},
        },
    )
    # Patched per class, not per instance: each instance reads its own label so two
    # terminals genuinely render different payloads.
    monkeypatch.setattr(
        plugin.DiscordRPCPlugin,
        "get_active_session_details",
        lambda self: {
            "title": f"session-{getattr(self, 'label', '?')}",
            "model": f"model-{getattr(self, 'label', '?')}",
            "total_tokens": 1000,
        },
    )
    instance = plugin.DiscordRPCPlugin()
    instance.label = label
    instance.rpc = _CapturingRPC()
    instance.is_connected = True
    return instance


def test_presence_follows_the_terminal_that_was_used_most_recently(plugin, monkeypatch, tmp_path):
    """Two terminals share one Discord presence slot, so ownership must follow activity.

    Discord exposes a single presence per client, so with two terminals ticking the
    slot is decided by which one published last and then held for as long as its own
    payload looked unchanged. Activity that does not alter the rendered payload --
    a second message arriving while the status is still "Thinking" -- must still
    hand the slot back to the terminal the user is actually working in.
    """
    runtime = tmp_path / "run"
    runtime.mkdir()
    first = _connected_instance(plugin, monkeypatch, "first", runtime)
    second = _connected_instance(plugin, monkeypatch, "second", runtime)

    def use(instance):
        """Drive real lifecycle hooks against ``instance`` as the active terminal."""
        monkeypatch.setattr(plugin, "_plugin_instance", instance)
        plugin._on_pre_llm()

    use(first)
    first.update_presence()
    assert len(first.rpc.updates) == 1
    assert "model-first" in first.rpc.updates[0]["state"]

    use(second)
    second.update_presence()
    first.update_presence()
    assert len(first.rpc.updates) == 1, "idle terminal must not republish"

    # The user works in the first terminal again. Its status and session data are
    # identical to what it last published, so a payload-keyed de-dup alone would
    # leave the slot showing the terminal the user moved away from.
    use(first)
    second.update_presence()
    first.update_presence()
    assert len(first.rpc.updates) == 2, "re-activated terminal must reclaim the slot"
    assert len(second.rpc.updates) == 1, "superseded terminal must yield"


def test_idle_terminals_do_not_thrash_the_presence_slot(plugin, monkeypatch, tmp_path):
    """Ownership is stable while nobody is working, so no redundant IPC writes."""
    runtime = tmp_path / "run"
    runtime.mkdir()
    first = _connected_instance(plugin, monkeypatch, "first", runtime)
    second = _connected_instance(plugin, monkeypatch, "second", runtime)

    first.mark_active()
    first.update_presence()
    second.update_presence()
    settled = first.rpc.updates.__len__() + len(second.rpc.updates)

    for _ in range(10):
        first.update_presence()
        second.update_presence()

    assert len(first.rpc.updates) + len(second.rpc.updates) == settled


def test_disconnect_releases_the_presence_slot(plugin, monkeypatch, tmp_path):
    """A terminal that exits must not keep the slot from the next one."""
    runtime = tmp_path / "run"
    runtime.mkdir()
    owner = _connected_instance(plugin, monkeypatch, "owner", runtime)
    owner.mark_active()
    owner.update_presence()
    assert os.path.exists(plugin._ownership_path())

    owner.disconnect()
    assert not os.path.exists(plugin._ownership_path())


def test_ownership_claim_is_scoped_per_user(plugin, monkeypatch, tmp_path):
    """The claim lives beside the Discord IPC socket, scoped to the current user."""
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    plugin._claim_owner("abc123", 1.0)

    path = plugin._ownership_path()
    assert os.path.dirname(path) == str(tmp_path)
    # Mirror _ownership_path()'s own scope fallback: os.getuid() does not exist on
    # Windows, where the claim is scoped by USERNAME instead.
    try:
        expected_scope = str(os.getuid())
    except AttributeError:
        expected_scope = os.environ.get("USERNAME", "user")
    assert f".{expected_scope}." in os.path.basename(path)
    assert plugin._read_owner() == {"token": "abc123", "stamp": 1.0}


def test_only_the_busy_terminal_publishes_within_one_profile(plugin, monkeypatch, tmp_path):
    """Same profile, one terminal running a tool: the idle one must not take the slot.

    Two terminals in one profile read the same session, so title/model/tokens agree and
    the *only* difference between their payloads is ``current_status`` -- which appears
    in both ``state_key`` and ``large_text``. A terminal running a tool therefore renders
    "[Running Terminal Command]" while an idle sibling renders "Active Session", and both
    decide they must publish. Measured on unfixed main, the idle sibling's write lands
    last and Discord displays the idle status while the user is actively working.
    """
    runtime = tmp_path / "run"
    runtime.mkdir()
    # Same label => same session => same profile. Status is the only difference.
    busy = _connected_instance(plugin, monkeypatch, "shared", runtime)
    idle = _connected_instance(plugin, monkeypatch, "shared", runtime)
    time.sleep(0.02)

    monkeypatch.setattr(plugin, "_plugin_instance", busy)
    plugin._on_pre_tool(tool_name="terminal")
    assert busy.current_status == "Running Terminal Command"
    assert idle.current_status == "Active"

    for _ in range(10):
        busy.update_presence()
        idle.update_presence()

    assert len(busy.rpc.updates) == 1
    assert idle.rpc.updates == [], "idle sibling published over the terminal in use"


def _herdr_stub(plugin, monkeypatch, focused_pane, own_pane="wA:pM"):
    """Serve a pane.list response with ``focused_pane`` marked focused.

    ``own_pane`` is this process' HERDR_PANE_ID, which is deliberately separate from
    the focused pane id: the question is never "what is focused" but "am I it".
    """
    monkeypatch.setenv("HERDR_ENV", "1")
    monkeypatch.setenv("HERDR_PANE_ID", own_pane)
    monkeypatch.setenv("HERDR_SOCKET_PATH", "/tmp/herdr-test.sock")
    panes = [{"pane_id": "wA:pM", "focused": False}, {"pane_id": "wA:pD", "focused": False}]
    if focused_pane:
        for pane in panes:
            if pane["pane_id"] == focused_pane:
                pane["focused"] = True
    monkeypatch.setattr(plugin, "_herdr_call", lambda *a, **k: {"result": {"panes": panes}})


def test_herdr_focus_beats_last_used(plugin, monkeypatch, tmp_path):
    """Under herdr the focused pane owns presence even if another pane ran last."""
    runtime = tmp_path / "run"
    runtime.mkdir()
    looking = _connected_instance(plugin, monkeypatch, "looking", runtime)
    other = _connected_instance(plugin, monkeypatch, "other", runtime)

    _herdr_stub(plugin, monkeypatch, focused_pane="wA:pD")
    looking.update_presence()
    other.update_presence()
    assert looking.rpc.updates == [] and other.rpc.updates == []

    _herdr_stub(plugin, monkeypatch, focused_pane="wA:pM")
    looking.update_presence()
    assert len(looking.rpc.updates) == 1, "focused pane must publish"
    assert other.rpc.updates == [], "unfocused pane published over the focused one"

    # Staying focused must not republish an unchanged payload every tick.
    looking.update_presence()
    assert len(looking.rpc.updates) == 1, "republished while continuously focused"


def test_herdr_unfocused_pane_leaves_presence_intact(plugin, monkeypatch, tmp_path):
    """Focus moving to a non-Hermes pane must not blank the last Hermes presence."""
    runtime = tmp_path / "run"
    runtime.mkdir()
    instance = _connected_instance(plugin, monkeypatch, "hermes", runtime)

    _herdr_stub(plugin, monkeypatch, focused_pane="wA:pM")
    instance.update_presence()
    assert len(instance.rpc.updates) == 1

    _herdr_stub(plugin, monkeypatch, focused_pane="wA:pD")
    for _ in range(5):
        instance.update_presence()
    assert len(instance.rpc.updates) == 1, "presence cleared when focus left Hermes"


def test_falls_back_to_activity_when_herdr_absent(plugin, monkeypatch, tmp_path):
    """No herdr, or an unreachable socket: the activity rule must still own the slot."""
    runtime = tmp_path / "run"
    runtime.mkdir()
    instance = _connected_instance(plugin, monkeypatch, "solo", runtime)
    for var in ("HERDR_ENV", "HERDR_PANE_ID", "HERDR_SOCKET_PATH"):
        monkeypatch.delenv(var, raising=False)

    assert plugin._herdr_focus() is None
    instance.update_presence()
    assert len(instance.rpc.updates) == 1

    monkeypatch.setenv("HERDR_ENV", "1")
    monkeypatch.setenv("HERDR_PANE_ID", "wA:pM")
    monkeypatch.setenv("HERDR_SOCKET_PATH", str(tmp_path / "missing.sock"))
    assert plugin._herdr_focus() is None, "unreachable socket must not guess focus"
    assert instance._claim_slot() is True, "activity ownership lost after herdr error"
    assert len(instance.rpc.updates) == 1, "unchanged payload must not be republished"


def test_herdr_focus_is_about_our_own_pane(plugin, monkeypatch):
    """Every pane asking "is it focused?" must not all get True.

    herdr's pane.current returns whichever pane the session has focused, regardless of
    who is asking, so reading its ``focused`` flag answers nothing about the caller.
    Measured against a live session, a process whose HERDR_PANE_ID was an unfocused
    pane still received focused=true, which would have had every pane contending for
    the slot at once. Ownership has to compare the focused pane id with our own.
    """
    _herdr_stub(plugin, monkeypatch, focused_pane="wA:pM", own_pane="wA:pM")
    assert plugin._herdr_focus() is True

    _herdr_stub(plugin, monkeypatch, focused_pane="wA:pM", own_pane="wA:pD")
    assert plugin._herdr_focus() is False, "unfocused pane believed it was focused"

    _herdr_stub(plugin, monkeypatch, focused_pane="wA:pD", own_pane="wA:pM")
    assert plugin._herdr_focus() is False, "focus moved away; stale pane still claimed it"


def test_herdr_focus_falls_back_when_nothing_is_focused(plugin, monkeypatch):
    """No pane focused, or a malformed reply: fall back rather than freeze presence."""
    _herdr_stub(plugin, monkeypatch, focused_pane=None)
    assert plugin._herdr_focus() is None

    monkeypatch.setattr(plugin, "_herdr_call", lambda *a, **k: {"result": {"panes": [
        {"pane_id": "wA:pM", "focused": True}, {"pane_id": "wA:pD", "focused": True}]}})
    assert plugin._herdr_focus() is None, "ambiguous multi-focus reply must not guess"

    monkeypatch.setattr(plugin, "_herdr_call", lambda *a, **k: None)
    assert plugin._herdr_focus() is None
