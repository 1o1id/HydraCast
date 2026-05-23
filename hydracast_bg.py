#!/usr/bin/env python3
"""
hydracast_bg.py  —  Background + system tray entry point for HydraCast.

Architecture
────────────
  Main thread  : pystray icon loop  (Windows REQUIRES tray on main thread)
  Worker thread: HydraCast core     (streams, web server, scheduler)

The --background flag is NOT passed to hydracast.main() here because
background mode on Windows re-launches the process and calls sys.exit(),
which would kill the tray before it starts.  Instead we suppress the TUI
directly by monkey-patching run_tui_loop to a no-op.
"""
import sys
import threading
import webbrowser
from pathlib import Path


# ── Base-dir must be set before any hc.* import ──────────────────────────────
def _setup_base_dir() -> None:
    from hc.constants import set_base_dir
    if getattr(sys, "frozen", False):
        set_base_dir(Path(sys.executable))
    else:
        set_base_dir(Path(__file__).resolve())


# ── Icon resolution ───────────────────────────────────────────────────────────
def _icon_path() -> Path:
    """
    Try several locations for HydraCast.ico so it works both frozen and
    from source.
    """
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).parent
    else:
        base = Path(__file__).resolve().parent

    candidates = [
        base / "resources" / "HydraCast.ico",
        base / "_internal" / "resources" / "HydraCast.ico",
        base / "resources" / "logo.png",
        base / "_internal" / "resources" / "logo.png",
    ]
    for p in candidates:
        if p.exists():
            return p
    return candidates[0]   # will be missing; _load_image handles it


def _load_image():
    """Return a PIL Image suitable for the system tray."""
    from PIL import Image

    path = _icon_path()
    if path.exists():
        try:
            img = Image.open(path)
            # pystray on Windows needs RGBA; convert if necessary.
            return img.convert("RGBA")
        except Exception:
            pass

    # Fallback: draw a simple green circle on transparent background.
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    from PIL import ImageDraw
    draw = ImageDraw.Draw(img)
    draw.ellipse([4, 4, 60, 60], fill=(34, 197, 94, 255))
    return img


# ── HydraCast worker ──────────────────────────────────────────────────────────
def _run_hydracast(shutdown_event: threading.Event) -> None:
    """
    Run the HydraCast core in a background thread.
    Signals shutdown_event when main() returns so the tray can exit.
    """
    try:
        # Suppress the TUI — we have no console and don't want one.
        import hc.tui as _tui_mod
        _tui_mod.run_tui_loop = lambda **kw: kw.get("shutdown_event",
                                                      threading.Event()).wait()

        import hydracast as _hc
        _hc.main()
    except SystemExit:
        pass
    except Exception as exc:
        import logging
        logging.getLogger(__name__).error("HydraCast worker crashed: %s", exc)
    finally:
        shutdown_event.set()


# ── Tray (runs on main thread) ────────────────────────────────────────────────
def _build_tray(shutdown_event: threading.Event, web_port: int):
    import pystray
    from pystray import MenuItem as Item

    def _open_web(icon, item):
        webbrowser.open(f"http://localhost:{web_port}")

    def _quit(icon, item):
        icon.stop()          # exits icon.run() on main thread → process ends
        shutdown_event.set()

    image = _load_image()
    menu = pystray.Menu(
        Item("Open Web UI", _open_web, default=True),
        pystray.Menu.SEPARATOR,
        Item("Quit HydraCast", _quit),
    )
    return pystray.Icon("HydraCast", image, "HydraCast", menu)


# ── Entry point ───────────────────────────────────────────────────────────────
def main() -> None:
    # Ensure hc package paths are correct before any import.
    if getattr(sys, "frozen", False):
        _HERE = Path(sys.executable).parent
    else:
        _HERE = Path(__file__).resolve().parent
    if str(_HERE) not in sys.path:
        sys.path.insert(0, str(_HERE))

    # Bootstrap runtime deps (same as hydracast.py does).
    # Import _bootstrap via hydracast module-level execution.
    # We do NOT pass --background; instead we suppress the TUI ourselves.
    # Remove any leftover --background flags so hydracast's argparse doesn't
    # try to re-launch / daemonize.
    sys.argv = [a for a in sys.argv
                if a not in ("--background", "-b")]

    _setup_base_dir()

    from hc.constants import get_web_port

    shutdown_event = threading.Event()

    # Start HydraCast on a background thread.
    worker = threading.Thread(
        target=_run_hydracast,
        args=(shutdown_event,),
        name="hydracast-worker",
        daemon=True,
    )
    worker.start()

    # Build and run the tray icon on the MAIN thread (Windows requirement).
    try:
        icon = _build_tray(shutdown_event, get_web_port())

        # If HydraCast crashes before the tray starts, stop immediately.
        def _watch_worker():
            shutdown_event.wait()
            icon.stop()
        threading.Thread(target=_watch_worker, daemon=True).start()

        icon.run()   # ← blocks main thread; returns when icon.stop() called

    except ImportError:
        # pystray not available — just wait for the worker to finish.
        shutdown_event.wait()

    # Wait for the worker to finish cleanly.
    worker.join(timeout=10)


if __name__ == "__main__":
    main()
