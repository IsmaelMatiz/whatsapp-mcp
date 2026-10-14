"""Dashboard de WhatsApp MCP.

Arranca el bridge si hace falta y abre una ventana con el estado de la
conexión (y el QR para vincular), los archivos descargados, la descarga
automática por chat, los nombres de contactos y la guía para Claude Desktop.

La interfaz es web (dashboard.html) servida en 127.0.0.1 con un token por
sesión. Se muestra en una ventana nativa con pywebview y, si este sistema no
tiene con qué (Linux sin GTK/Qt), en el navegador en modo aplicación.
"""

import json
import os
import secrets
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import install  # noqa: E402  (funciones compartidas con el instalador)
import whatsapp  # noqa: E402

STORE_DIR = install.BRIDGE_DIR / "store"
BRIDGE_API = f"http://127.0.0.1:{install.BRIDGE_PORT}/api"
HTML = (HERE / "dashboard.html").read_text(encoding="utf-8")
TOKEN = secrets.token_urlsafe(24)
WHISPER_MODELS = ["tiny", "base", "small", "medium", "large-v3"]

# Con el navegador como ventana no hay forma de saber cuándo se cierra: la
# página avisa cada pocos segundos y el servidor se apaga si deja de hacerlo.
last_ping = time.monotonic()
IDLE_SHUTDOWN_SECONDS = 180


def db():
    return sqlite3.connect(whatsapp.MESSAGES_DB_PATH)


def chat_dir(jid):
    return STORE_DIR / jid.replace(":", "_")


def dir_size(path):
    total = 0
    for root, _, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


def open_path(path):
    path = str(path)
    if install.SYSTEM == "Windows":
        os.startfile(path)
    elif install.SYSTEM == "Darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def reveal_path(path):
    """Abre la carpeta que contiene el archivo, seleccionándolo cuando se puede."""
    if install.SYSTEM == "Windows":
        subprocess.Popen(["explorer", "/select,", str(path)])
    elif install.SYSTEM == "Darwin":
        subprocess.Popen(["open", "-R", str(path)])
    else:
        open_path(Path(path).parent)


# --- Datos ------------------------------------------------------------------

def bridge_status():
    try:
        with urllib.request.urlopen(f"{BRIDGE_API}/status", timeout=2) as resp:
            return {"running": True, **json.load(resp)}
    except Exception:
        # Un bridge viejo no tiene /api/status pero sí puede estar corriendo
        return {"running": install.bridge_running()}


def claude_status():
    dirs, _ = install.claude_desktop_dirs()
    configs = []
    for d in dirs:
        path = d / "claude_desktop_config.json"
        entry = None
        if path.exists():
            try:
                entry = json.loads(path.read_text(encoding="utf-8-sig") or "{}").get("mcpServers", {}).get("whatsapp")
            except json.JSONDecodeError:
                configs.append({"path": str(path), "configured": False, "error": "JSON inválido"})
                continue
        configs.append({
            "path": str(path),
            "exists": path.exists(),
            "configured": entry is not None,
            "whisper_model": (entry or {}).get("env", {}).get("WHISPER_MODEL", "small"),
        })
    return {"configs": configs, "claude_code": shutil.which("claude") is not None}


def overview():
    stats = {"chats": 0, "messages": 0, "last_message": None, "auto_download": 0, "contact_names": 0}
    try:
        conn = db()
        try:
            conn.execute(whatsapp.AUTO_DOWNLOAD_TABLE)
            conn.execute(whatsapp.CONTACT_NAMES_TABLE)
            stats["chats"] = conn.execute("SELECT COUNT(*) FROM chats").fetchone()[0]
            stats["messages"] = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            stats["last_message"] = conn.execute("SELECT MAX(timestamp) FROM messages").fetchone()[0]
            stats["auto_download"] = conn.execute("SELECT COUNT(*) FROM auto_download_chats").fetchone()[0]
            stats["contact_names"] = conn.execute("SELECT COUNT(*) FROM contact_names").fetchone()[0]
        finally:
            conn.close()
    except sqlite3.Error:
        pass  # Todavía no hay base de datos: el bridge la crea al arrancar
    return {
        "bridge": bridge_status(),
        "bridge_installed": install.BRIDGE_BIN.exists(),
        "autostart": install.autostart_configured(),
        "ffmpeg": shutil.which("ffmpeg") is not None,
        "stats": stats,
        "system": install.SYSTEM,
    }


def list_chats(query):
    conn = db()
    try:
        conn.execute(whatsapp.AUTO_DOWNLOAD_TABLE)
        sql = """
            SELECT c.jid, c.name, c.last_message_time, a.jid IS NOT NULL,
                   (SELECT COUNT(*) FROM messages m WHERE m.chat_jid = c.jid AND m.media_type != '' AND m.media_type IS NOT NULL)
            FROM chats c LEFT JOIN auto_download_chats a ON a.jid = c.jid
            WHERE c.jid NOT LIKE '%@broadcast' AND c.jid NOT LIKE '%@newsletter'
        """
        params = []
        if query:
            sql += " AND (LOWER(c.name) LIKE LOWER(?) OR c.jid LIKE ?)"
            params += [f"%{query}%", f"%{query}%"]
        sql += " ORDER BY a.jid IS NULL, c.last_message_time DESC LIMIT 200"
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    chats = []
    for jid, name, last, auto, media in rows:
        folder = chat_dir(jid)
        chats.append({
            "jid": jid, "name": name or jid.split("@")[0], "last_message": last,
            "is_group": jid.endswith("@g.us"), "auto_download": bool(auto),
            "media_count": media, "has_folder": folder.is_dir(),
        })
    return chats


def media_summary():
    conn = db()
    try:
        rows = conn.execute("""
            SELECT m.local_path, m.media_type, m.timestamp, m.chat_jid, c.name
            FROM messages m LEFT JOIN chats c ON c.jid = m.chat_jid
            WHERE m.local_path IS NOT NULL AND m.local_path != ''
            ORDER BY m.timestamp DESC LIMIT 60
        """).fetchall()
    finally:
        conn.close()
    recent = []
    for path, media_type, ts, jid, name in rows:
        p = Path(path)
        if not p.is_absolute():
            p = install.BRIDGE_DIR / p
        if p.exists():
            recent.append({"path": str(p), "name": p.name, "type": media_type, "timestamp": ts,
                           "chat": name or jid.split("@")[0], "size": p.stat().st_size})
    folders = []
    if STORE_DIR.is_dir():
        for d in STORE_DIR.iterdir():
            if d.is_dir():
                folders.append({"jid": d.name, "size": dir_size(d)})
    return {"store": str(STORE_DIR), "total_size": sum(f["size"] for f in folders), "recent": recent[:30]}


def set_whisper_model(model):
    if model not in WHISPER_MODELS:
        return False, "Modelo desconocido"
    changed = 0
    for d in install.claude_desktop_dirs()[0]:
        path = d / "claude_desktop_config.json"
        if not path.exists():
            continue
        config = json.loads(path.read_text(encoding="utf-8-sig") or "{}")
        entry = config.get("mcpServers", {}).get("whatsapp")
        if entry is None:
            continue
        env = entry.setdefault("env", {})
        if model == "small":
            env.pop("WHISPER_MODEL", None)  # el valor por defecto
            if not env:
                entry.pop("env")
        else:
            env["WHISPER_MODEL"] = model
        path.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        changed += 1
    if not changed:
        return False, "Primero conecta WhatsApp MCP con Claude Desktop"
    return True, "Guardado. Reinicia Claude Desktop para usar el modelo nuevo."


def configure_claude():
    entry = {"command": install.uv_path(), "args": ["--directory", str(HERE), "run", "main.py"]}
    install.configure_claude_desktop(entry)
    return claude_status()


def restart_bridge():
    if not install.stop_bridge(interactive=False):
        return False, "El bridge está abierto en una terminal. Ciérralo ahí (Ctrl+C) e inténtalo de nuevo."
    if install.start_bridge():
        return True, "Bridge reiniciado"
    return False, "El bridge no arrancó"


def set_autostart(enabled):
    if enabled:
        install.ASSUME_YES = True  # setup_autostart pregunta; aquí ya se decidió
        install.setup_autostart()
    else:
        install.remove_autostart()
    return install.autostart_configured()


# --- Servidor ---------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def allowed(self):
        # Solo esta ventana: el Host evita DNS rebinding y el token, que otras webs
        # abiertas en el navegador puedan usar la API.
        host = self.headers.get("Host", "")
        if host != f"127.0.0.1:{self.server.server_port}":
            return False
        if self.path.startswith("/api/"):
            return secrets.compare_digest(self.headers.get("X-Token", ""), TOKEN) or \
                parse_qs(urlparse(self.path).query).get("token", [""])[0] == TOKEN
        return True

    def body(self):
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length) or b"{}")

    def do_GET(self):
        global last_ping
        if not self.allowed():
            return self.send_error(403)
        url = urlparse(self.path)
        if url.path == "/":
            body = HTML.replace("__TOKEN__", TOKEN).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            return self.wfile.write(body)
        last_ping = time.monotonic()
        query = parse_qs(url.query)
        if url.path == "/api/overview":
            return self.send_json(overview())
        if url.path == "/api/qr.png":
            try:
                with urllib.request.urlopen(f"{BRIDGE_API}/qr.png", timeout=2) as resp:
                    data = resp.read()
            except Exception:
                return self.send_error(404)
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return self.wfile.write(data)
        if url.path == "/api/chats":
            return self.send_json(list_chats(query.get("q", [""])[0]))
        if url.path == "/api/media":
            return self.send_json(media_summary())
        if url.path == "/api/contacts":
            return self.send_json({"unnamed": whatsapp.list_unnamed_contacts(),
                                   "named": whatsapp.list_contact_names()})
        if url.path == "/api/claude":
            return self.send_json(claude_status())
        self.send_error(404)

    def do_POST(self):
        if not self.allowed():
            return self.send_error(403)
        path = urlparse(self.path).path
        data = self.body()
        try:
            if path == "/api/bridge/start":
                ok = install.start_bridge()
                return self.send_json({"ok": ok, "message": "Bridge corriendo" if ok else "El bridge no arrancó"})
            if path == "/api/bridge/restart":
                ok, msg = restart_bridge()
                return self.send_json({"ok": ok, "message": msg})
            if path == "/api/autostart":
                return self.send_json({"ok": True, "autostart": set_autostart(bool(data.get("enabled")))})
            if path == "/api/auto-download":
                ok, msg = whatsapp.set_auto_download(data["jid"], bool(data.get("enabled")))
                return self.send_json({"ok": ok, "message": msg})
            if path == "/api/contact-name":
                ok, msg = whatsapp.set_contact_name(data["jid"], data.get("name", ""))
                return self.send_json({"ok": ok, "message": msg})
            if path == "/api/open":
                target = data.get("what")
                if target == "store":
                    STORE_DIR.mkdir(parents=True, exist_ok=True)
                    open_path(STORE_DIR)
                elif target == "chat":
                    folder = chat_dir(data["jid"])
                    if not folder.resolve().is_relative_to(STORE_DIR.resolve()) or not folder.is_dir():
                        return self.send_json({"ok": False, "message": "Este chat no tiene archivos descargados"})
                    open_path(folder)
                elif target in ("file", "reveal"):
                    p = Path(data["path"]).resolve()
                    if not p.is_relative_to(STORE_DIR.resolve()) or not p.exists():
                        return self.send_json({"ok": False, "message": "Archivo no encontrado"})
                    (open_path if target == "file" else reveal_path)(p)
                elif target == "claude-config":
                    config = Path(data["path"])
                    config.parent.mkdir(parents=True, exist_ok=True)
                    open_path(config.parent)
                else:
                    return self.send_json({"ok": False, "message": "Destino desconocido"}, 400)
                return self.send_json({"ok": True})
            if path == "/api/claude/configure":
                return self.send_json({"ok": True, **configure_claude()})
            if path == "/api/whisper-model":
                ok, msg = set_whisper_model(data.get("model"))
                return self.send_json({"ok": ok, "message": msg})
        except Exception as e:  # el dashboard muestra el error en vez de quedarse colgado
            return self.send_json({"ok": False, "message": str(e)}, 500)
        self.send_error(404)


def browser_app(url):
    """Abre la URL como ventana de aplicación (sin pestañas) si hay un navegador
    basado en Chromium; si no, en una pestaña normal."""
    candidates = ["msedge", "chrome", "google-chrome", "google-chrome-stable", "chromium",
                  "chromium-browser", "brave", "brave-browser", "microsoft-edge"]
    if install.SYSTEM == "Windows":
        for base in (os.environ.get("PROGRAMFILES(X86)"), os.environ.get("PROGRAMFILES"), os.environ.get("LOCALAPPDATA")):
            if base:
                candidates += [str(Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe"),
                               str(Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe")]
    elif install.SYSTEM == "Darwin":
        candidates += ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                       "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"]
    for exe in candidates:
        path = shutil.which(exe) or (exe if os.path.isfile(exe) else None)
        if path:
            subprocess.Popen([path, f"--app={url}", "--window-size=1100,780"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
    webbrowser.open(url)


def main():
    if not install.bridge_running():
        threading.Thread(target=install.start_bridge, daemon=True).start()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    url = f"http://127.0.0.1:{server.server_port}/"
    threading.Thread(target=server.serve_forever, daemon=True).start()

    try:
        import webview
        webview.create_window("WhatsApp MCP", url, width=1100, height=780, min_size=(720, 560))
        webview.start()
        return  # la ventana se cerró
    except Exception as e:
        print(f"Sin ventana nativa ({e}); se abre en el navegador.")

    browser_app(url)
    global last_ping
    last_ping = time.monotonic()
    while time.monotonic() - last_ping < IDLE_SHUTDOWN_SECONDS:
        time.sleep(5)


if __name__ == "__main__":
    main()
