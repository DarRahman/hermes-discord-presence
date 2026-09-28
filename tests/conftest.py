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
