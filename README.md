# WhatsApp MCP Server

A Model Context Protocol (MCP) server that connects Claude (or Cursor) to your **personal WhatsApp account**. Fork of [lharries/whatsapp-mcp](https://github.com/lharries/whatsapp-mcp).

It uses the WhatsApp Web multidevice API through [whatsmeow](https://github.com/tulir/whatsmeow). Messages are stored locally in SQLite and only reach the LLM when it calls a tool.

![WhatsApp MCP](./example-use.png)

> **Caution:** like many MCP servers, this one is exposed to [the lethal trifecta](https://simonwillison.net/2025/Jun/16/the-lethal-trifecta/): a prompt injection in a message could lead to private data exfiltration.

## Features

- **Read and search** chats, messages and contacts, including groups.
- **Send** text, files (images, videos, documents) and voice messages.
- **Download media** on demand. Images and stickers are returned so the AI can see them; videos and GIFs return one frame. Captions are saved as the message text.
- **Auto-download** new media for the chats you choose, before WhatsApp's links expire.
- **Transcribe voice notes locally** with [faster-whisper](https://github.com/SYSTRAN/faster-whisper). The audio never leaves your computer.
- **Name contacts** that only show up as a number, so every tool returns the name instead of the LLM having to remember it.
- **Dashboard** to link WhatsApp with the QR code, watch the sync, open downloaded files, pick auto-download chats, name contacts and connect Claude Desktop.

### Tools

| Tool | What it does |
| --- | --- |
| `search_contacts` | Search contacts by name or phone number |
| `list_chats` / `get_chat` | List chats or get one chat |
| `get_direct_chat_by_contact` / `get_contact_chats` | Find the chats of a contact |
| `list_messages` | List messages with filters (chat, sender, date, text) |
| `get_message_context` / `get_last_interaction` | Messages around a message, or the last one with a contact |
| `send_message` | Send text to a phone number or group JID |
| `send_file` | Send an image, video, document or raw audio |
| `send_audio_message` | Send audio as a playable voice message |
| `download_media` | Download a message's media and return its local path |
| `transcribe_audio` | Transcribe a voice note to text |
| `set_auto_download` / `list_auto_download_chats` | Manage per-chat automatic download |
| `set_contact_name` | Save a name for a contact that only has a number |

## Installation

### Quick install (Windows, Linux, macOS)

1. Download the repository: **Code > Download ZIP** on GitHub, and unzip it. Or clone it:

   ```bash
   git clone https://github.com/IsmaelMatiz/whatsapp-mcp.git
   ```

2. Run the installer:
   - **Windows:** double-click `setup.bat`.
   - **Linux / macOS:** run `./setup.sh` in a terminal.

3. Scan the QR code shown in the dashboard with your phone (WhatsApp > Linked devices > Link a device).

4. Quit Claude Desktop completely (from the system tray or menu bar, not just the window) and open it again.

The installer sets up [uv](https://docs.astral.sh/uv/) and Python, downloads the prebuilt bridge, offers to install FFmpeg, downloads the transcription model, adds the server to Claude Desktop (and Claude Code if installed), starts the bridge at login, and creates a **WhatsApp MCP** shortcut. It asks before each optional step. Run it again to update.

## Dashboard

Open **WhatsApp MCP** from the Start menu, the desktop, the app menu or `~/Applications` (or run `uv run dashboard.py` in `whatsapp-mcp-server/`). It starts the bridge if it isn't running and shows:

- **Home:** bridge and WhatsApp status, the QR code when the device needs linking (also after WhatsApp logs the session out), sync progress and the start-at-login switch.
- **Files:** the downloaded media folder and the latest files, to open them or show them in their folder.
- **Auto-download:** a switch per chat.
- **Contacts:** contacts shown only by number, with the name WhatsApp knows as a suggestion.
- **Claude Desktop:** one-click setup, a step-by-step guide and the transcription model.

It opens in a native window ([pywebview](https://pywebview.flowrl.com/)); on Linux without GTK or Qt it opens as a Chromium app window or a browser tab.

### Manual install

**Prerequisites:** Go, Python 3.11+, [uv](https://docs.astral.sh/uv/) and, optionally, FFmpeg (lets the AI see images and send voice messages).

1. **Start the bridge** and scan the QR code. It must stay running.

   ```bash
   cd whatsapp-bridge
   go run .
   ```

2. **Add the server to your client.** Replace the paths (`which uv` gives the first one):

   ```json
   {
     "mcpServers": {
       "whatsapp": {
         "command": "/path/to/uv",
         "args": ["--directory", "/path/to/whatsapp-mcp/whatsapp-mcp-server", "run", "main.py"]
       }
     }
   }
   ```

   - Claude Desktop: `claude_desktop_config.json` in its configuration folder.
   - Cursor: `~/.cursor/mcp.json`.

3. **Restart the client.** WhatsApp shows up as an available integration.

### Publishing a release

The installer downloads the bridge from the latest GitHub release. Pushing a tag builds it for every platform (`.github/workflows/release.yml`):

```bash
git tag v1.0.0 && git push origin v1.0.0
```

Without a release, the installer compiles the bridge if Go is installed.

## Configuration

Transcription is set with environment variables (`env` in the client config). The model is downloaded by the installer or on first use.

| Variable | Default | Options |
| --- | --- | --- |
| `WHISPER_MODEL` | `small` (~460 MB) | `base` is faster, `medium` is more accurate |
| `WHISPER_DEVICE` | `cpu` | `cuda`, `auto` |
| `WHISPER_COMPUTE_TYPE` | `int8` | any faster-whisper quantization |

Media is saved in `whatsapp-bridge/store/<chat JID>/`, named after the message ID.

## How it works

- **Go bridge** (`whatsapp-bridge/`): connects to WhatsApp, keeps the SQLite database in `whatsapp-bridge/store/` up to date, and sends messages and downloads media.
- **Python MCP server** (`whatsapp-mcp-server/`): exposes the tools, reads the database and calls the bridge.

## Troubleshooting

- **Is the bridge running?** The dashboard shows it and can start or restart it.
- **No QR code:** open the dashboard; it shows the code whenever the device needs linking. If a session is active, the bridge reconnects without one.
- **Windows, Microsoft Store version of Claude:** its *Edit Config* button opens a file the app ignores. Use the installer or the dashboard's *Connect automatically*, which write to the file the app reads.
- **Device limit reached:** remove a linked device from WhatsApp on your phone.
- **No messages yet:** the first history sync can take several minutes.
- **Out of sync:** delete `whatsapp-bridge/store/messages.db` and `whatsapp-bridge/store/whatsapp.db`, then restart the bridge and scan again.
- **Client issues:** see the [MCP documentation](https://modelcontextprotocol.io/quickstart/server#claude-for-desktop-integration-issues).
