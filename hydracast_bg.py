#!/usr/bin/env python3
"""
hydracast_bg.py  —  Background + system tray entry point for HydraCast.

Launches HydraCast in background mode (no TUI) and shows a system tray
icon with a right-click menu:
  • Open Web UI   — opens the browser to the Web UI
  • Quit          — shuts down HydraCast cleanly

Requires: pystray, Pillow  (added to requirements.txt)
"""
import sys
import threading
import webbrowser
from pathlib import Path

# ── Force background mode before hydracast.py's argparse runs ────────────────
if "--background" not in sys.argv and "-b" not in sys.argv:
    sys.argv.insert(1, "--background")

# ── Resolve icon path (works frozen and from source) ─────────────────────────
def _icon_path() -> Path:
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).parent
    else:
        base = Path(__file__).resolve().parent
    return base / "resources" / "HydraCast.ico"


def _load_image():
    """Return a PIL Image for the tray icon."""
    from PIL import Image
    ico = _icon_path()
    if ico.exists():
        return Image.open(ico)
    # Fallback: plain green 64×64 square so the tray always shows something.
    img = Image.new("RGB", (64, 64), color=(34, 197, 94))
    return img


# ── Tray icon ─────────────────────────────────────────────────────────────────
def _run_tray(shutdown_event: threading.Event, web_port: int) -> None:
    """
    Create and run the pystray icon.  Blocks until the user clicks Quit
    or shutdown_event is set externally (e.g. SIGTERM).
    """
    try:
        import pystray
        from pystray import MenuItem as Item
    except ImportError:
        # pystray not available — run silently without a tray icon.
        shutdown_event.wait()
        return

    def _open_web(icon, item):
        webbrowser.open(f"http://localhost:{web_port}")

    def _quit(icon, item):
        icon.stop()
        shutdown_event.set()

    image = _load_image()
    menu  = pystray.Menu(
        Item("Open Web UI", _open_web, default=True),
        pystray.Menu.SEPARATOR,
        Item("Quit HydraCast", _quit),
    )
    icon = pystray.Icon("HydraCast", image, "HydraCast", menu)

    # Watch for external shutdown (e.g. SIGTERM) and stop the icon.
    def _watch():
        shutdown_event.wait()
        icon.stop()
    threading.Thread(target=_watch, daemon=True).start()

    icon.run()          # blocks until icon.stop() is called


# ── Main ──────────────────────────────────────────────────────────────────────
def main() -> None:
    # Import hydracast internals AFTER sys.argv is patched.
    # set_base_dir is already called inside hydracast when it is imported,
    # so we only need to grab the pieces we use here.
    import hydracast as _hc          # runs module-level _bootstrap()
    from hc.constants import get_web_port, set_base_dir, WEB_PORT

    # Mirror the frozen/source base-dir logic from hydracast.py.
    if getattr(sys, "frozen", False):
        set_base_dir(Path(sys.executable))
    else:
        set_base_dir(Path(__file__))

    shutdown_event = threading.Event()

    # Patch hydracast's internal _shutdown so our tray Quit also stops streams.
    # We start the tray in a daemon thread and let _hc.main() run normally;
    # when _hc.main() returns (SIGTERM / web quit) we stop the tray too.
    tray_thread = threading.Thread(
        target=_run_tray,
        args=(shutdown_event, get_web_port()),
        daemon=True,
        name="tray",
    )
    tray_thread.start()

    # Run the normal HydraCast main loop (returns on shutdown).
    _hc.main()

    # Signal the tray to close if main() returned first.
    shutdown_event.set()
    tray_thread.join(timeout=3)


if __name__ == "__main__":
    main()
