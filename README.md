# Hermes Agent Discord Presence

Show your active **Hermes Agent** session on Discord! Displays your current session topic, active AI model, total tokens used, and live activity status in real-time.

---

## Platform Support

| Platform | Status |
| --- | --- |
| Windows (x64) | Tested |
| macOS (Apple Silicon / Intel) | Untested |
| Linux (x64 / ARM64) | Untested |

*Note: macOS and Linux support the same Hermes plugin architecture and Python IPC bindings, but have not been formally verified yet. Bug reports and testing feedback are welcome.*

---

## Features

- **Session Duration**: Real-time timer showing how long your Hermes session has been active.
- **Session Title**: Displays the current workspace thread title from Hermes `state.db`.
- **Model Tracking**: Shows the exact active LLM powering Hermes (e.g. `ag/gemini-3.6-flash-high`, `claude-sonnet-4`).
- **Token Counter**: Real-time total token usage counter (e.g. `51.5k tokens`, `5.1M tokens`).
- **Live Activity Status**: Shows active state (`[Thinking]`, `[Running terminal]`) directly in line 1.
- **Zero Background Daemons**: Runs natively inside the Hermes Agent process. Automatically connects on startup and disconnects cleanly when you exit Hermes.

---

## Requirements

- Discord desktop application running locally.
- Hermes Agent (Desktop GUI or CLI) installed.
- Python 3.9 or higher with `pypresence` library.

---

## Installation

### 1. Install Dependencies

Install `pypresence` and `pyyaml` into your Python environment:

```cmd
pip install pypresence pyyaml
```

### 2. Install as a Native Hermes Plugin

Clone this repository into your local Hermes plugins folder:

```cmd
mkdir "%LOCALAPPDATA%\hermes\plugins\hermes-discord-rpc"
git clone https://github.com/DarRahman/hermes-discord-presence.git "%LOCALAPPDATA%\hermes\plugins\hermes-discord-rpc"
```

### 3. Enable the Plugin

Add `hermes-discord-rpc` to the `plugins.enabled` list inside your Hermes configuration file (`%LOCALAPPDATA%\hermes\config.yaml`):

```yaml
plugins:
  enabled:
    - hermes-discord-rpc
```

Restart Hermes Agent to apply changes.

---

## How It Works

The plugin operates natively within the Hermes process via three core mechanisms:

1. **Lifecycle Hook Binding**: Registers with internal Hermes hooks (`pre_llm_call`, `pre_tool_call`, `post_tool_call`, `on_session_end`).
2. **SQLite State Querying**: Executes fast, read-only queries against `state.db` to fetch the session title, model, and total tokens associated with the latest message timestamp (`MAX(timestamp)`).
3. **Background Sync Thread**: Runs a non-blocking daemon thread polling Discord's local IPC pipe every 3 seconds to ensure status updates remain responsive.

### Active Session Tracking Behavior
Hermes Agent writes session data to `state.db` upon message interaction. Therefore, the Rich Presence status updates to your current active session when a message is sent or processed. Simply clicking through historical session tabs in the UI without chatting will not trigger a database write or change the displayed session.

---

## Discord Presence Display

```
+-------------------------------------------------------+
| Hermes Agent                                          |
| [Thinking] Refactoring Auth Middleware                |
| ag/gemini-3.6-flash-high • 5.1M tokens                |
| 00:32:15 elapsed                                      |
+-------------------------------------------------------+
```

---

## Advanced: Using a Custom Discord App

By default, this plugin uses a pre-configured Discord Application ID (`1530932637546451074`). If you prefer to use your own custom Discord app branding:

1. Navigate to the [Discord Developer Portal](https://discord.com/developers/applications).
2. Click **New Application** and enter your desired app name (e.g. `Hermes Agent`).
3. Copy the **Application ID** under **OAuth2 -> General**.
4. Upload a 512x512 PNG icon named `hermes_logo` under **Rich Presence -> Art Assets**.
5. Update `CLIENT_ID` in `__init__.py` with your new Application ID.

---

## Uninstallation

To remove the plugin:

1. Remove `hermes-discord-rpc` from `plugins.enabled` in `%LOCALAPPDATA%\hermes\config.yaml`.
2. Delete the plugin directory:
   ```cmd
   rmdir /s /q "%LOCALAPPDATA%\hermes\plugins\hermes-discord-rpc"
   ```
3. Restart Hermes Agent.

---

## Privacy

This integration runs 100% locally on your machine. It reads session metadata directly from your local `state.db` SQLite database and sends Rich Presence updates strictly to your local Discord IPC socket. No external telemetry or remote data collection is performed.

---

## Contributing

Contributions are welcome! Feel free to open issues or submit pull requests to improve platform compatibility, asset options, or state resolution.

1. Fork the Repository.
2. Create your Feature Branch (`git checkout -b feature/improvement`).
3. Commit your Changes (`git commit -m 'Add feature'`).
4. Push to the Branch (`git push origin feature/improvement`).
5. Open a Pull Request.

---

## License

Distributed under the MIT License. See [LICENSE](LICENSE) for details.

---

## Acknowledgments

- [Nous Research](https://nousresearch.com) for Hermes Agent.
- [pypresence](https://github.com/qwertyquerty/pypresence) for Discord IPC Python bindings.
