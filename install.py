"""Instalador de whatsapp-mcp. Lo ejecutan setup.bat (Windows) y setup.sh
(Linux/macOS) a través de uv, que ya trae el Python y las dependencias.

Pasos: descarga el bridge ya compilado, ofrece instalar ffmpeg, descarga el
modelo de transcripción, registra el servidor en Claude Desktop (y Claude Code
si está instalado), configura el bridge para que arranque al iniciar sesión y
vincula WhatsApp con el código QR.

Se puede ejecutar varias veces: sirve también para actualizar y para volver a
escanear el QR cuando WhatsApp cierra la sesión.

Opciones:
  --yes    acepta todas las preguntas con la opción por defecto
  --build  compila el bridge con Go en vez de descargarlo
"""

import json
import os
import platform
import plistlib
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = "IsmaelMatiz/whatsapp-mcp"
ROOT = Path(__file__).resolve().parent
BRIDGE_DIR = ROOT / "whatsapp-bridge"
SERVER_DIR = ROOT / "whatsapp-mcp-server"
SYSTEM = platform.system()  # "Windows", "Linux" o "Darwin"
EXE = ".exe" if SYSTEM == "Windows" else ""
BRIDGE_BIN = BRIDGE_DIR / f"whatsapp-bridge{EXE}"
BRIDGE_PORT = 8080

SERVICE_NAME = "whatsapp-bridge"
LAUNCHD_LABEL = "com.whatsapp-mcp.bridge"
SYSTEMD_UNIT = Path.home() / ".config" / "systemd" / "user" / f"{SERVICE_NAME}.service"
LAUNCHD_PLIST = Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"

MODEL_SIZES = {"tiny": "75 MB", "base": "145 MB", "small": "460 MB", "medium": "1,5 GB", "large-v3": "3 GB"}

ASSUME_YES = "--yes" in sys.argv
FORCE_BUILD = "--build" in sys.argv


def step(n, total, title):
    print(f"\n\033[1m[{n}/{total}] {title}\033[0m")


def ok(msg):
    print(f"  ✓ {msg}")


def warn(msg):
    print(f"  ! {msg}")


def ask(question, default=True):
    if ASSUME_YES or not sys.stdin.isatty():
        return default
    hint = "[S/n]" if default else "[s/N]"
    answer = input(f"  {question} {hint} ").strip().lower()
    return default if not answer else answer in ("s", "si", "sí", "y", "yes")


def run(cmd, **kwargs):
    return subprocess.run(cmd, **kwargs).returncode == 0


def startup_vbs():
    return Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / f"{SERVICE_NAME}.vbs"


# --- Bridge -----------------------------------------------------------------

def bridge_running():
    try:
        with socket.create_connection(("127.0.0.1", BRIDGE_PORT), timeout=1):
            return True
    except OSError:
        return False


def stop_bridge():
    """Detiene el bridge si está corriendo, para poder actualizarlo y vincularlo."""
    if not bridge_running():
        ok("El bridge no está corriendo")
        return
    if SYSTEM == "Linux" and shutil.which("systemctl"):
        run(["systemctl", "--user", "stop", SERVICE_NAME], stderr=subprocess.DEVNULL)
    elif SYSTEM == "Darwin" and LAUNCHD_PLIST.exists():
        run(["launchctl", "unload", str(LAUNCHD_PLIST)], stderr=subprocess.DEVNULL)
    elif SYSTEM == "Windows":
        run(["taskkill", "/IM", BRIDGE_BIN.name, "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(10):
        if not bridge_running():
            ok("Bridge detenido")
            return
        time.sleep(0.5)
    while bridge_running():
        input("  El bridge está abierto en otra ventana. Ciérralo (Ctrl+C en esa ventana) y presiona Enter... ")
    ok("Bridge detenido")


def asset_name():
    os_name = {"Windows": "windows", "Linux": "linux", "Darwin": "darwin"}.get(SYSTEM)
    arch = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(platform.machine().lower())
    if not os_name or not arch:
        return None
    return f"whatsapp-bridge-{os_name}-{arch}{EXE}"


def download(url, dest):
    tmp = dest.with_name(dest.name + ".part")
    with urllib.request.urlopen(url, timeout=60) as resp, open(tmp, "wb") as out:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        while chunk := resp.read(1 << 16):
            out.write(chunk)
            done += len(chunk)
            if total:
                print(f"\r  Descargando... {done * 100 // total}%", end="", flush=True)
    print()
    os.replace(tmp, dest)


def build_bridge():
    print("  Compilando el bridge con Go (la primera vez tarda un par de minutos)...")
    env = {**os.environ, "CGO_ENABLED": "0"}
    return run(["go", "build", "-o", BRIDGE_BIN.name, "."], cwd=BRIDGE_DIR, env=env)


def install_bridge():
    name = asset_name()
    if name and not FORCE_BUILD:
        url = f"https://github.com/{REPO}/releases/latest/download/{name}"
        try:
            download(url, BRIDGE_BIN)
            BRIDGE_BIN.chmod(0o755)
            ok(f"Bridge descargado ({name})")
            return True
        except (urllib.error.URLError, OSError) as e:
            warn(f"No se pudo descargar el bridge: {e}")
    if shutil.which("go") and build_bridge():
        ok("Bridge compilado")
        return True
    if BRIDGE_BIN.exists():
        warn("Se usará el bridge que ya estaba instalado")
        return True
    warn("No hay bridge. Instala Go (https://go.dev/dl/) y vuelve a ejecutar el instalador.")
    return False


# --- ffmpeg -----------------------------------------------------------------

def install_ffmpeg():
    if shutil.which("ffmpeg"):
        ok("ffmpeg ya está instalado")
        return
    print("  ffmpeg permite que la IA vea las imágenes y enviar notas de voz. Es opcional.")
    if SYSTEM == "Windows" and shutil.which("winget"):
        cmd = ["winget", "install", "--id", "Gyan.FFmpeg", "-e",
               "--accept-source-agreements", "--accept-package-agreements"]
    elif SYSTEM == "Darwin" and shutil.which("brew"):
        cmd = ["brew", "install", "ffmpeg"]
    elif shutil.which("pacman"):
        cmd = ["sudo", "pacman", "-S", "--needed", "ffmpeg"]
    elif shutil.which("apt-get"):
        cmd = ["sudo", "apt-get", "install", "-y", "ffmpeg"]
    elif shutil.which("dnf"):
        cmd = ["sudo", "dnf", "install", "-y", "ffmpeg"]
    elif shutil.which("zypper"):
        cmd = ["sudo", "zypper", "install", "-y", "ffmpeg"]
    else:
        warn("Instálalo a mano desde https://ffmpeg.org/download.html si lo quieres.")
        return
    if ask(f"¿Instalar ffmpeg con «{' '.join(cmd)}»?"):
        if run(cmd):
            ok("ffmpeg instalado")
        else:
            warn("No se pudo instalar ffmpeg; el resto funciona igual.")


# --- Modelo de transcripción ------------------------------------------------

def download_model():
    model = os.environ.get("WHISPER_MODEL", "small")
    size = MODEL_SIZES.get(model, "varios cientos de MB")
    if not ask(f"¿Descargar ahora el modelo de transcripción «{model}» (~{size})?"):
        warn("Se descargará la primera vez que transcribas un audio.")
        return
    sys.path.insert(0, str(SERVER_DIR))
    try:
        import transcription
        transcription._get_model()
        ok("Modelo listo")
    except Exception as e:
        warn(f"No se pudo descargar el modelo ({e}); se intentará al transcribir el primer audio.")


# --- Claude -----------------------------------------------------------------

def uv_path():
    uv = os.environ.get("UV") or shutil.which("uv")
    return str(Path(uv).resolve()) if uv else "uv"


def claude_desktop_dirs():
    """Carpetas de configuración de Claude Desktop. Devuelve (carpetas, crear_si_falta)."""
    if SYSTEM == "Windows":
        dirs = [Path(os.environ["APPDATA"]) / "Claude"]
        # La versión de la Microsoft Store guarda la configuración en una ruta virtualizada
        packages = Path(os.environ.get("LOCALAPPDATA", "")) / "Packages"
        if packages.is_dir():
            dirs += [p / "LocalCache" / "Roaming" / "Claude" for p in packages.glob("Claude_*")]
        return dirs, True
    if SYSTEM == "Darwin":
        return [Path.home() / "Library" / "Application Support" / "Claude"], True
    config_home = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return [config_home / "Claude"], False


def configure_claude_desktop(entry):
    dirs, create = claude_desktop_dirs()
    written = False
    for d in dirs:
        if not d.is_dir() and not create:
            continue
        d.mkdir(parents=True, exist_ok=True)
        path = d / "claude_desktop_config.json"
        config = {}
        if path.exists():
            text = path.read_text(encoding="utf-8-sig")
            try:
                config = json.loads(text) if text.strip() else {}
            except json.JSONDecodeError:
                warn(f"{path} no es un JSON válido; no lo toqué. Arréglalo y vuelve a ejecutar el instalador.")
                continue
            shutil.copy2(path, path.with_name(path.name + ".bak"))
        servers = config.setdefault("mcpServers", {})
        previous = servers.get("whatsapp", {})
        servers["whatsapp"] = {**entry, **({"env": previous["env"]} if "env" in previous else {})}
        path.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        ok(f"Claude Desktop configurado ({path})")
        written = True
    if not written:
        warn("No encontré Claude Desktop.")


def configure_claude_code(entry):
    claude = shutil.which("claude")
    if not claude:
        return
    if run([claude, "mcp", "get", "whatsapp"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL):
        ok("Claude Code ya tiene el servidor whatsapp")
        return
    if ask("Encontré Claude Code. ¿Agregarle también el servidor?"):
        cmd = [claude, "mcp", "add", "--scope", "user", "whatsapp", "--", entry["command"], *entry["args"]]
        if run(cmd, stdout=subprocess.DEVNULL):
            ok("Claude Code configurado")
        else:
            warn("No se pudo agregar a Claude Code.")


# --- Arranque automático ----------------------------------------------------

def setup_autostart():
    """Registra el bridge para que arranque al iniciar sesión. Devuelve la
    función que lo arranca ahora mismo, o None si no se configuró."""
    if not ask("¿Arrancar el bridge solo cada vez que inicies sesión? (recomendado)"):
        return None
    if SYSTEM == "Windows":
        vbs = startup_vbs()
        vbs.write_text(
            'Set shell = CreateObject("WScript.Shell")\r\n'
            f'shell.CurrentDirectory = "{BRIDGE_DIR}"\r\n'
            f'shell.Run """{BRIDGE_BIN}""", 0, False\r\n',
            encoding="utf-8",
        )
        ok(f"Agregado al inicio de Windows ({vbs.name})")
        return lambda: subprocess.Popen(["wscript.exe", str(vbs)])
    if SYSTEM == "Darwin":
        LAUNCHD_PLIST.parent.mkdir(parents=True, exist_ok=True)
        with open(LAUNCHD_PLIST, "wb") as f:
            plistlib.dump({
                "Label": LAUNCHD_LABEL,
                "ProgramArguments": [str(BRIDGE_BIN)],
                "WorkingDirectory": str(BRIDGE_DIR),
                "RunAtLoad": True,
                "KeepAlive": {"SuccessfulExit": False},
            }, f)
        ok("Agregado a los servicios de inicio de sesión (launchd)")
        return lambda: run(["launchctl", "load", "-w", str(LAUNCHD_PLIST)])
    if shutil.which("systemctl"):
        SYSTEMD_UNIT.parent.mkdir(parents=True, exist_ok=True)
        SYSTEMD_UNIT.write_text(
            "[Unit]\n"
            "Description=WhatsApp bridge for whatsapp-mcp\n"
            "After=network-online.target\n\n"
            "[Service]\n"
            f"WorkingDirectory={BRIDGE_DIR}\n"
            f'ExecStart="{BRIDGE_BIN}"\n'
            "Restart=on-failure\n"
            "RestartSec=10\n\n"
            "[Install]\n"
            "WantedBy=default.target\n",
            encoding="utf-8",
        )
        run(["systemctl", "--user", "daemon-reload"])
        run(["systemctl", "--user", "enable", SERVICE_NAME], stderr=subprocess.DEVNULL)
        ok(f"Servicio de usuario creado ({SYSTEMD_UNIT})")
        return lambda: run(["systemctl", "--user", "start", SERVICE_NAME])
    warn("No sé configurar el arranque automático en este sistema; arranca el bridge a mano.")
    return None


# --- Vinculación ------------------------------------------------------------

def pair_and_start(start_service):
    """Arranca el bridge en esta ventana para mostrar el QR. Cuando conecta,
    lo pasa a segundo plano si hay arranque automático."""
    print("  Si aparece un código QR, escanéalo desde el teléfono:")
    print("  WhatsApp > Dispositivos vinculados > Vincular un dispositivo.\n")
    proc = subprocess.Popen([str(BRIDGE_BIN)], cwd=BRIDGE_DIR, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    connected = threading.Event()
    showed_qr = threading.Event()

    def relay():
        for raw in proc.stdout:
            line = raw.decode("utf-8", "replace")
            sys.stdout.write("  " + line)
            sys.stdout.flush()
            if "Scan this QR code" in line:
                showed_qr.set()
            if "Connected to WhatsApp" in line:
                connected.set()

    threading.Thread(target=relay, daemon=True).start()
    try:
        while proc.poll() is None and not connected.wait(1):
            pass
    except KeyboardInterrupt:
        proc.terminate()
        warn("Vinculación cancelada.")
        return False
    if not connected.is_set():
        warn("El bridge no pudo conectarse. Vuelve a ejecutar el instalador para intentarlo de nuevo.")
        return False

    if not start_service:
        print("\n  Deja esta ventana abierta mientras uses WhatsApp con Claude (Ctrl+C para cerrar).")
        try:
            proc.wait()
        except KeyboardInterrupt:
            proc.terminate()
        return True

    if showed_qr.is_set():
        print("\n  Sincronizando tus chats durante un minuto antes de pasar a segundo plano...")
        try:
            time.sleep(60)
        except KeyboardInterrupt:
            pass
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
    start_service()
    ok("El bridge quedó corriendo en segundo plano")
    return True


def main():
    if SYSTEM == "Windows":
        os.system("")  # activa los colores ANSI en la consola clásica de Windows
    print("\033[1mInstalador de WhatsApp MCP\033[0m")
    total = 6

    step(1, total, "Bridge de WhatsApp")
    stop_bridge()
    if not install_bridge():
        sys.exit(1)

    step(2, total, "ffmpeg")
    install_ffmpeg()

    step(3, total, "Modelo de transcripción de audios")
    download_model()

    step(4, total, "Conexión con Claude")
    entry = {"command": uv_path(), "args": ["--directory", str(SERVER_DIR), "run", "main.py"]}
    configure_claude_desktop(entry)
    configure_claude_code(entry)

    step(5, total, "Arranque automático")
    start_service = setup_autostart()

    step(6, total, "Vincular WhatsApp")
    paired = pair_and_start(start_service)

    print("\n\033[1mListo.\033[0m" if paired else "\n\033[1mInstalación terminada, falta vincular WhatsApp.\033[0m")
    print("  Cierra Claude Desktop por completo (también desde la bandeja del sistema) y vuelve a abrirlo.")
    print("  Si WhatsApp cierra la sesión (pasa cada pocas semanas), vuelve a ejecutar este instalador.")


if __name__ == "__main__":
    main()
