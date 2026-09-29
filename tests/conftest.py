"""Shared fixtures for the plugin test suite.

Hermes loads a standalone plugin from its ``plugin.yaml`` plus root
``__init__.py``, and this repository's directory name
(``hermes-discord-presence``) is not a valid Python identifier. Tests therefore
load the root module explicitly by file path instead of relying on package
discovery.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
MODULE_NAME = "hermes_discord_rpc_under_test"


def _load_plugin_module():
    """Import the plugin's root ``__init__.py`` as a module."""
    spec = importlib.util.spec_from_file_location(MODULE_NAME, PLUGIN_ROOT / "__init__.py")
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise RuntimeError(f"cannot load plugin module from {PLUGIN_ROOT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def plugin():
    """The plugin module under test."""
    return _load_plugin_module()


@pytest.fixture(autouse=True)
def no_real_discord(monkeypatch, tmp_path):
    """Never let a test reach the developer's running Discord client.

    ``set_status()`` ends in ``update_presence()``, which calls ``connect()``. So a test
    that merely pokes the status API - even one that only asserts on
    ``current_status`` - goes looking for the real ``discord-ipc-N`` socket and, on a
    machine with Discord running, publishes real presence. That socket lives in
    ``XDG_RUNTIME_DIR`` (on macOS, ``$TMPDIR``), inherited from the ambient
    environment, so this leaks by default rather than only when a test asks for it.

    ``test_hold_timer_preserves_status`` did exactly this, and the leak was not merely
    a side effect: reaching a real client made ``update_presence`` do real work, which
    reset ``current_status`` to ``"Active"`` and failed the test's own assertion. The
    suite therefore passed on CI (no Discord) and failed on any developer machine that
    happened to have Discord running.

    Point ``XDG_RUNTIME_DIR`` at an empty directory. ``connect()`` then finds no socket
    and degrades to disconnected, which is the same path the plugin already takes when
    Discord is not running. Tests exercising the connected path set their own
    ``XDG_RUNTIME_DIR`` and stub the client, and their override wins.
    """
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "empty-runtime"))


@pytest.fixture(autouse=True)
def no_herdr_env(monkeypatch):
    """Clear herdr's ambient pane variables for every test.

    Ownership prefers herdr's focus answer when ``HERDR_ENV`` is set, so a developer
    running the suite from inside a herdr pane would otherwise get the real focus
    state injected into tests that are exercising the activity fallback. Tests that
    want herdr behaviour set the variables themselves.
    """
    for var in ("HERDR_ENV", "HERDR_PANE_ID", "HERDR_SOCKET_PATH", "HERDR_TAB_ID",
                "HERDR_WORKSPACE_ID", "HERDR_BIN_PATH"):
        monkeypatch.delenv(var, raising=False)
