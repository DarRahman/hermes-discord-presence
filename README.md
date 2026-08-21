# Hermes Agent Discord Presence

[![CI](https://github.com/DarRahman/hermes-discord-presence/actions/workflows/validate.yml/badge.svg)](https://github.com/DarRahman/hermes-discord-presence/actions/workflows/validate.yml)
[![Plugin Manifest](https://img.shields.io/badge/hermes--plugin-v1.0.0-orange)](plugin.yaml)
[![Python Version](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

Native Discord Rich Presence (RPC) integration for Hermes Agent. Displays active workspace session titles, LLM model names, live token consumption, elapsed time, and real-time execution states directly on your Discord profile.

![Hermes Discord Presence Preview](assets/preview.png)

---

## Features

- **In-Process Native Execution**: Runs directly inside the Hermes Agent process using lifecycle hooks. No external background daemons or separate system services.
- **Zero Setup**: Uses a pre-configured Discord Application ID with default brand assets. Works out of the box.
- **Live Status & Activity**: Real-time activity tags (`[Thinking]`, `[Running tool]`, `[Processing]`).
- **Session & Token Tracking**: Reads session titles, active LLM model names, and total token usage from local Hermes state storage (`state.db`).
- **Clean Session Lifecycle**: Connects on launch and disconnects cleanly when Hermes shuts down.

---

## Requirements

- Discord Desktop client running locally.
- Hermes Agent installed (CLI or Desktop GUI).
- Python 3.10+ with `pypresence` and `pyyaml`.

---

## Installation

### 1. Install Dependencies

Install required Python packages:

```bash
pip install pypresence pyyaml
```

### 2. Install Plugin via Hermes CLI

Install directly from GitHub repository:

```bash
hermes plugins install DarRahman/hermes-discord-presence
```

### 3. Enable Plugin

Enable the plugin in Hermes:

```bash
hermes plugins enable hermes-discord-rpc
```

Or add `hermes-discord-rpc` to `plugins.enabled` in `%LOCALAPPDATA%\hermes\config.yaml` (`~/.hermes/config.yaml` on Linux/macOS):

```yaml
plugins:
  enabled:
    - hermes-discord-rpc
```

Restart Hermes Agent to apply changes.

---

## Custom Discord Application (Optional)

To use custom Discord Application ID and art assets, edit `config.yaml`:

```yaml
discord_client_id: "YOUR_DISCORD_CLIENT_ID"
update_interval: 3.0
presence:
  large_image: "your_asset_key"
  large_text: "Hermes Agent"
```

---

## Architecture & Verification

The plugin registers with Hermes lifecycle hooks:
- `pre_llm_call`: Updates status to `[Thinking]`
- `pre_tool_call`: Updates status to `[Running <tool_name>]`
- `post_tool_call`: Updates status to `[Processing]`
- `on_session_end`: Resets status to `Active`

Verify plugin manifest contract:

```bash
hermes plugins doctor .
```

---

## License

This project is licensed under the [MIT License](LICENSE).
