"""
hc/web_handlers_post.py  —  POST dispatch + upload + backup/restore for WebHandler.

Mixed into WebHandler in web.py.
"""
from __future__ import annotations

import hashlib
import logging
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from hc.constants import APP_VER, MEDIA_DIR, SUPPORTED_EXTS, UPLOAD_MAX_BYTES
from hc.models import CameraConfig, OneShotEvent, PlaylistItem, StreamConfig
from hc.utils import _fmt_duration, _safe_path

log = logging.getLogger(__name__)

# Local alias for the shim (imported at call sites from hc.web to avoid
# circular-import issues at module load time)
_FILE_OPS = {
    "file_rename", "file_delete", "file_delete_dir", "file_move", "file_copy",
}


class _PostHandlersMixin:
    """Mixed into WebHandler — POST dispatch + upload + backup/restore."""

    # ── Main dispatch ─────────────────────────────────────────────────────────

    def _dispatch(self, action: str, data: Dict[str, Any]) -> None:  # noqa: C901
        from hc.web import _WEB_MANAGER, CSVManager  # type: ignore

        # File-manager actions are handled by the FileManager mixin
        if action in _FILE_OPS:
            self._handle_file_op(action, data)
            return

        mgr = _WEB_MANAGER
        if not mgr:
            self._json({"ok": False, "msg": "Manager not ready"})
            return

        # ── Stream control ────────────────────────────────────────────────────
        if action == "start":
            st = mgr.get_state(str(data.get("name", "")))
            if st:
                mgr.start(st.config.name)
                self._json({"ok": True, "msg": f"Starting {st.config.name}"})
            else:
                self._json({"ok": False, "msg": "Stream not found"})

        elif action == "stop":
            st = mgr.get_state(str(data.get("name", "")))
            if st:
                mgr.stop(st.config.name)
                self._json({"ok": True, "msg": f"Stopping {st.config.name}"})
            else:
                self._json({"ok": False, "msg": "Stream not found"})

        elif action == "restart":
            st = mgr.get_state(str(data.get("name", "")))
            if st:
                mgr.restart(st.config.name)
                self._json({"ok": True, "msg": f"Restarting {st.config.name}"})
            else:
                self._json({"ok": False, "msg": "Stream not found"})

        elif action == "start_all":
            mgr.start_all()
            self._json({"ok": True, "msg": "Starting all streams"})

        elif action == "stop_all":
            for _st in mgr.states:
                try:
                    mgr.stop(_st.config.name)
                except Exception:
                    pass
            self._json({"ok": True, "msg": "Stopped all streams"})

        elif action == "restart_all":
            for st in mgr.states:
                try:
                    mgr.restart(st.config.name)
                except Exception:
                    pass
            self._json({"ok": True, "msg": "Restarting all streams"})

        elif action == "skip_next":
            st = mgr.get_state(str(data.get("name", "")))
            if st:
                _w = mgr.get_worker(st.config.name)
                if _w: _w.skip_to_next()
                self._json({"ok": True, "msg": f"Skipping in {st.config.name}"})
            else:
                self._json({"ok": False, "msg": "Stream not found"})

        elif action == "seek":
            st = mgr.get_state(str(data.get("name", "")))
            try:
                secs = float(data.get("seconds", 0))
                if secs < 0:
                    raise ValueError("negative")
            except (TypeError, ValueError):
                self._json({"ok": False, "msg": "Invalid seek position"})
                return
            if st:
                # Cap seek to 1 second before end-of-file to prevent broken-pipe
                # crash when FFmpeg starts exactly at EOF.
                dur = st.duration or 0
                if dur > 1 and secs >= dur:
                    secs = max(0.0, dur - 1.0)
                _w = mgr.get_worker(st.config.name)
                if _w: _w.seek(secs)
                self._json({"ok": True, "msg": f"Seeking to {_fmt_duration(secs)}"})
            else:
                self._json({"ok": False, "msg": "Stream not found"})

        # ── Config update ─────────────────────────────────────────────────────
        elif action == "update_config":
            try:
                name_s = str(data.get("name", "")).strip()
                if not name_s:
                    self._json({"ok": False, "msg": "Missing stream name"})
                    return
                st = mgr.get_state(name_s)
                if not st:
                    self._json({"ok": False, "msg": "Stream not found"})
                    return
                cfg = st.config
                new_port = int(data.get("port", cfg.port))
                if not (1024 <= new_port <= 65535):
                    raise ValueError(f"Port {new_port} out of range")
                cfg.port = new_port
                sp = str(data.get("stream_path", cfg.stream_path)).strip()
                if sp:
                    cfg.stream_path = sp
                vbr = str(data.get("video_bitrate", "")).strip()
                if vbr:
                    cfg.video_bitrate = CSVManager._sanitize_bitrate(vbr, cfg.video_bitrate)
                abr = str(data.get("audio_bitrate", "")).strip()
                if abr:
                    cfg.audio_bitrate = CSVManager._sanitize_bitrate(abr, cfg.audio_bitrate)
                if "enabled" in data:
                    cfg.enabled = bool(data["enabled"])
                if "shuffle" in data:
                    cfg.shuffle = bool(data["shuffle"])
                if "hls_enabled" in data:
                    cfg.hls_enabled = bool(data["hls_enabled"])
                # Weekdays
                raw_wd = str(data.get("weekdays", "")).strip()
                if raw_wd:
                    cfg.weekdays = CSVManager.parse_weekdays(raw_wd)
                # Files
                raw_files = str(data.get("files", "")).strip()
                if raw_files:
                    parsed = CSVManager.parse_files(raw_files.replace("\n", ";"))
                    if parsed:
                        cfg.playlist = parsed
                # Compliance
                if "compliance_enabled" in data:
                    cfg.compliance_enabled = bool(data["compliance_enabled"])
                if "compliance_start" in data:
                    cfg.compliance_start = CSVManager._sanitize_hms(
                        str(data["compliance_start"]))
                if "compliance_loop" in data:
                    cfg.compliance_loop = bool(data["compliance_loop"])
                # ── Hybrid source switching ───────────────────────────────────
                if "source_mode" in data:
                    cfg.source_mode = str(data["source_mode"]).strip() or "playlist"
                if "camera_id" in data:
                    cfg.camera_id = str(data["camera_id"]).strip() or None
                if "camera_windows" in data and isinstance(data["camera_windows"], list):
                    cfg.camera_windows = data["camera_windows"]

                CSVManager.save([s.config for s in mgr.states])
                self._json({"ok": True, "msg": f"Config saved for '{name_s}'"})
            except Exception as exc:
                self._json({"ok": False, "msg": str(exc)})

        # ── Event scheduling ──────────────────────────────────────────────────
        elif action == "schedule_event":
            try:
                stream_name = str(data.get("stream_name", "")).strip()
                file_path   = str(data.get("file_path", "")).strip()
                play_at     = str(data.get("play_at", "")).strip()
                post_action = str(data.get("post_action", "resume")).strip()
                start_pos   = CSVManager._sanitize_hms(str(data.get("start_pos", "00:00:00")))
                loop_count  = int(data.get("loop_count", 0))
                if not stream_name:
                    raise ValueError("Stream name is required")
                if mgr.get_state(stream_name) is None:
                    raise ValueError(f"Stream '{stream_name}' not found")
                dt = None
                for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
                    try:
                        dt = datetime.strptime(play_at, fmt); break
                    except ValueError:
                        continue
                if dt is None:
                    raise ValueError("Invalid datetime format")
                if dt <= datetime.now():
                    raise ValueError("Cannot schedule an event in the past")
                fp   = Path(file_path)
                safe = _safe_path(fp, MEDIA_DIR())
                if safe is None and not fp.exists():
                    raise ValueError("File not found or path outside media directory")
                ev_id = hashlib.md5(
                    f"{stream_name}{play_at}{file_path}".encode()
                ).hexdigest()[:8]
                if any(e.event_id == ev_id for e in mgr.events):
                    raise ValueError("An identical event is already scheduled")
                ev = OneShotEvent(
                    event_id    = ev_id,
                    stream_name = stream_name,
                    file_path   = fp,
                    play_at     = dt,
                    post_action = post_action,
                    start_pos   = start_pos,
                    loop_count  = loop_count,
                )
                mgr.add_event(ev)
                self._json({"ok": True, "msg": f"Event scheduled for {dt.strftime('%Y-%m-%d %H:%M')}"})
            except Exception as exc:
                self._json({"ok": False, "msg": str(exc)})

        elif action == "update_event":
            try:
                ev_id = str(data.get("event_id", "")).strip()
                if not ev_id:
                    raise ValueError("Missing event_id")
                ev = next((e for e in mgr.events if e.event_id == ev_id), None)
                if ev is None:
                    raise ValueError(f"Event '{ev_id}' not found")
                if ev.played:
                    raise ValueError("Cannot edit an already-played event")
                # Update fields if provided
                if "stream_name" in data:
                    sn = str(data["stream_name"]).strip()
                    if mgr.get_state(sn) is None:
                        raise ValueError(f"Stream '{sn}' not found")
                    ev.stream_name = sn
                if "file_path" in data:
                    fp = Path(str(data["file_path"]).strip())
                    safe = _safe_path(fp, MEDIA_DIR())
                    if safe is None and not fp.exists():
                        raise ValueError("File not found or path outside media directory")
                    ev.file_path = fp
                if "play_at" in data:
                    play_at_s = str(data["play_at"]).strip()
                    dt = None
                    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
                        try:
                            dt = datetime.strptime(play_at_s, fmt); break
                        except ValueError:
                            continue
                    if dt is None:
                        raise ValueError("Invalid datetime format")
                    if dt <= datetime.now():
                        raise ValueError("Cannot reschedule an event to the past")
                    ev.play_at = dt
                if "post_action" in data:
                    ev.post_action = str(data["post_action"]).strip()
                if "start_pos" in data:
                    ev.start_pos = CSVManager._sanitize_hms(str(data["start_pos"]))
                if "loop_count" in data:
                    ev.loop_count = int(data["loop_count"])
                if "comment" in data:
                    try:
                        ev.comment = str(data["comment"]).strip()[:500]
                    except AttributeError:
                        pass   # older event model without comment field
                # Persist
                try:
                    from hc.json_manager import JSONManager
                    JSONManager._save_events(mgr.events)
                except Exception as _pe:
                    log.warning("update_event: could not persist: %s", _pe)
                self._json({"ok": True, "msg": "Event updated"})
            except Exception as exc:
                self._json({"ok": False, "msg": str(exc)})

        elif action == "delete_event":
            ev_id = str(data.get("event_id", "")).strip()
            if not ev_id:
                self._json({"ok": False, "msg": "Missing event_id"})
                return
            removed = mgr.remove_event(ev_id)
            self._json({"ok": removed, "msg": "Event deleted" if removed else "Event not found"})

        elif action == "cancel_event":
            # Stop a CURRENTLY RUNNING one-shot event and resume
            # the compliance file / playlist at the correct seek position.
            name = str(data.get("name", "")).strip()
            st = mgr.get_state(name)
            if not st:
                self._json({"ok": False, "msg": f"Stream '{name}' not found"})
                return
            if not st.oneshot_active:
                self._json({"ok": False, "msg": "No event is currently running on this stream"})
                return
            w = mgr.get_worker(name)
            if not w:
                self._json({"ok": False, "msg": "Worker not found"})
                return
            threading.Thread(
                target=w.cancel_oneshot, daemon=True,
                name=f"cancel-event-{st.config.port}",
            ).start()
            self._json({"ok": True, "msg": f"Event cancelled — resuming on '{name}'"})

        # ── Stream CRUD ───────────────────────────────────────────────────────
        elif action == "create_stream":
            try:
                name_s = str(data.get("name", "")).strip()
                if not name_s or len(name_s) > 64:
                    raise ValueError(f"Invalid stream name: '{name_s}'")
                if not re.fullmatch(r"[\w\-. ]+", name_s):
                    raise ValueError(
                        "Stream name may only contain letters, numbers, "
                        "spaces, hyphens, dots and underscores."
                    )
                port = int(data.get("port", 0))
                if not (1024 <= port <= 65535):
                    raise ValueError(f"Port {port} out of range (1024-65535).")
                stream_path = str(data.get("stream_path", "")).strip()
                folder_source_raw = str(data.get("folder_source") or "").strip()
                folder_source = None
                playlist: List[PlaylistItem] = []
                if folder_source_raw:
                    from hc.folder_scanner import scan_folder, SortMode
                    folder_source = Path(folder_source_raw)
                    if not folder_source.is_dir():
                        raise ValueError(f"Folder not found: '{folder_source_raw}'")
                    playlist, warnings = scan_folder(folder_source, SortMode.ALPHA_FWD)
                    for w in warnings:
                        log.warning("create_stream folder scan: %s", w)
                    if not playlist:
                        raise ValueError(f"No supported media files in '{folder_source_raw}'")
                else:
                    raw_files = str(data.get("files", "")).strip().replace("\n", ";")
                    playlist  = CSVManager.parse_files(raw_files)
                    if not playlist:
                        raise ValueError("At least one valid file path is required.")
                comp_start = CSVManager._sanitize_hms(
                    str(data.get("compliance_start", "06:00:00")))
                cfg = StreamConfig(
                    name=name_s, port=port, playlist=playlist,
                    weekdays=CSVManager.parse_weekdays(str(data.get("weekdays", "all"))),
                    enabled=bool(data.get("enabled", True)),
                    shuffle=bool(data.get("shuffle", False)),
                    stream_path=stream_path,
                    video_bitrate=CSVManager._sanitize_bitrate(
                        str(data.get("video_bitrate", "2500k")), "2500k"),
                    audio_bitrate=CSVManager._sanitize_bitrate(
                        str(data.get("audio_bitrate", "128k")), "128k"),
                    hls_enabled=bool(data.get("hls_enabled", False)),
                    folder_source=folder_source,
                    compliance_enabled=bool(data.get("compliance_enabled", False)),
                    compliance_start=comp_start,
                    compliance_loop=bool(data.get("compliance_loop", False)),
                    # ── Hybrid source switching ───────────────────────────
                    source_mode=str(data.get("source_mode", "playlist")).strip() or "playlist",
                    camera_id=str(data.get("camera_id", "")).strip() or None,
                    camera_windows=data.get("camera_windows", []) if isinstance(data.get("camera_windows"), list) else [],
                )
                mgr.add_stream(cfg)
                path_label = f"/{stream_path}" if stream_path else "/"
                self._json({
                    "ok":  True,
                    "msg": f"Stream '{name_s}' created on port {port} (path: {path_label}).",
                })
            except Exception as exc:
                self._json({"ok": False, "msg": str(exc)})

        elif action == "delete_stream":
            try:
                name_s = str(data.get("name", "")).strip()
                if not name_s:
                    raise ValueError("Missing stream name.")
                mgr.remove_stream(name_s)
                self._json({"ok": True, "msg": f"Stream '{name_s}' deleted."})
            except Exception as exc:
                self._json({"ok": False, "msg": str(exc)})

        elif action == "delete_played_events":
            ids = data.get("event_ids", [])
            if not isinstance(ids, list):
                self._json({"ok": False, "msg": "event_ids must be a list"})
                return
            id_set = set(str(i).strip() for i in ids)
            count = mgr.remove_events(id_set)
            self._json({"ok": True, "msg": f"Removed {count} event(s)"})

        elif action == "fire_event_now":
            ev_id = str(data.get("event_id", "")).strip()
            if not ev_id:
                self._json({"ok": False, "msg": "Missing event_id"})
                return
            ok = mgr.fire_event_now(ev_id)
            self._json({"ok": ok, "msg": "Event fired" if ok else "Event not found or stream not running"})

        # ── Legacy file/folder ops (via upload tab) ───────────────────────────
        elif action == "delete_file":
            from hc.web import _invalidate_lib_cache  # type: ignore
            raw_path = str(data.get("path", "")).strip()
            if not raw_path:
                self._json({"ok": False, "msg": "Missing path"})
                return
            p    = Path(raw_path)
            safe = _safe_path(p, MEDIA_DIR())
            if safe is None or not safe.is_file():
                self._json({"ok": False, "msg": "File not in media dir or not found"})
                return
            try:
                safe.unlink()
                _invalidate_lib_cache()
                self._json({"ok": True, "msg": f"Deleted {safe.name}"})
            except Exception as exc:
                self._json({"ok": False, "msg": str(exc)})

        elif action == "create_subdir":
            raw = str(data.get("name", "")).strip()
            if not raw or re.search(r'[/\\<>"|?*\x00]', raw) or ".." in raw:
                self._json({"ok": False, "msg": "Invalid folder name"})
                return
            target = MEDIA_DIR() / raw
            safe   = _safe_path(target, MEDIA_DIR())
            if safe is None:
                self._json({"ok": False, "msg": "Path traversal denied"})
                return
            try:
                safe.mkdir(parents=True, exist_ok=True)
                self._json({"ok": True, "msg": f"Created: {raw}"})
            except Exception as exc:
                self._json({"ok": False, "msg": str(exc)})

        # ── Mail config ───────────────────────────────────────────────────────
        elif action == "save_mail_config":
            try:
                import json as _json
                from hc.constants import BASE_DIR
                mode      = str(data.get("mode", "smtp")).strip()
                to_addrs  = data.get("to_addrs", [])
                if not isinstance(to_addrs, list) or not to_addrs:
                    raise ValueError("to_addrs must be a non-empty list")
                smtp_port = int(data.get("smtp_port", 587))
                if not (1 <= smtp_port <= 65535):
                    raise ValueError(f"Invalid SMTP port: {smtp_port}")
                path = BASE_DIR() / "mail_config.json"
                password = str(data.get("password", ""))
                if password in ("••••••••", ""):
                    try:
                        existing = _json.loads(path.read_text(encoding="utf-8"))
                        password = existing.get("password", "")
                    except Exception:
                        password = ""
                cfg = {
                    "enabled":       bool(data.get("enabled", False)),
                    "mode":          mode,
                    "to_addrs":      [str(a).strip() for a in to_addrs if str(a).strip()],
                    "on_error":      bool(data.get("on_error", True)),
                    "on_stop":       bool(data.get("on_stop", True)),
                    "cooldown_secs": max(0, int(data.get("cooldown_secs", 300))),
                    "smtp_host":     str(data.get("smtp_host", "")).strip(),
                    "smtp_port":     smtp_port,
                    "use_tls":       bool(data.get("use_tls", True)),
                    "username":      str(data.get("username", "")).strip(),
                    "password":      password,
                    "from_addr":     str(data.get("from_addr", "")).strip(),
                    "ms_client_id":  str(data.get("ms_client_id", "")).strip(),
                    "ms_username":   str(data.get("ms_username", "")).strip(),
                }
                path.write_text(_json.dumps(cfg, indent=4, ensure_ascii=False), encoding="utf-8")
                log.info("mail_config.json updated (mode=%s enabled=%s)", mode, cfg["enabled"])
                self._json({"ok": True, "msg": "mail_config.json saved"})
            except Exception as exc:
                self._json({"ok": False, "msg": str(exc)})

        elif action == "test_mail_alert":
            try:
                to_addr = str(data.get("to_addr", "")).strip() or None
                from hc.mailer import test_alert
                ok, err = test_alert(to_addr)
                if ok:
                    self._json({"ok": True,  "msg": "Test email sent — check your inbox."})
                else:
                    self._json({"ok": False, "msg": err or "Test failed — check server logs."})
            except Exception as exc:
                self._json({"ok": False, "msg": f"Test error: {exc}"})

        elif action == "gmail_oauth2_start":
            try:
                from hc.mailer import start_gmail_oauth2_flow
                ok, msg = start_gmail_oauth2_flow()
                self._json({"ok": ok, "msg": msg})
            except Exception as exc:
                self._json({"ok": False, "msg": str(exc)})

        elif action == "gmail_oauth2_revoke":
            try:
                from hc.mailer import revoke_gmail_token
                ok, msg = revoke_gmail_token()
                self._json({"ok": ok, "msg": msg})
            except Exception as exc:
                self._json({"ok": False, "msg": str(exc)})

        elif action == "microsoft_oauth2_start":
            try:
                client_id = str(data.get("ms_client_id", "")).strip()
                if not client_id:
                    from hc.constants import BASE_DIR
                    import json as _json
                    try:
                        saved = _json.loads((BASE_DIR() / "mail_config.json").read_text("utf-8"))
                        client_id = saved.get("ms_client_id", "").strip()
                    except Exception:
                        pass
                if not client_id:
                    self._json({"ok": False, "msg": "Enter Application (Client) ID and save config first."})
                    return
                from hc.mailer import start_microsoft_oauth2_flow
                ok, instructions = start_microsoft_oauth2_flow(client_id)
                if ok:
                    from hc.mailer import _ms_flow_state  # type: ignore[attr-defined]
                    self._json({
                        "ok":               True,
                        "msg":              instructions,
                        "user_code":        _ms_flow_state.get("user_code", ""),
                        "verification_uri": _ms_flow_state.get("verification_uri",
                                            "https://microsoft.com/devicelogin"),
                    })
                else:
                    self._json({"ok": False, "msg": instructions})
            except Exception as exc:
                self._json({"ok": False, "msg": str(exc)})

        elif action == "microsoft_oauth2_revoke":
            try:
                from hc.constants import BASE_DIR
                import json as _json
                cfg2: dict = {}
                try:
                    cfg2 = _json.loads((BASE_DIR() / "mail_config.json").read_text("utf-8"))
                except Exception:
                    pass
                from hc.mailer import revoke_microsoft_token
                ok, msg = revoke_microsoft_token(cfg2)
                self._json({"ok": ok, "msg": msg})
            except Exception as exc:
                self._json({"ok": False, "msg": str(exc)})

        elif action == "backup":
            self._handle_backup(data)

        elif action == "restore":
            self._handle_restore(data)

        elif action == "reset":
            self._handle_reset(data)

        elif action == "restart_process":
            self._handle_restart_process()

        # ── Camera registry ───────────────────────────────────────────────────
        elif action == "add_camera":
            self._camera_add(data)

        elif action == "edit_camera":
            self._camera_edit(data)

        elif action == "delete_camera":
            self._camera_delete(data)

        # ── Source switching ──────────────────────────────────────────────────
        elif action == "source_switch":
            self._source_switch(data)

        else:
            self._json({"ok": False, "msg": f"Unknown action: {action}"}, 404)

    # ── Camera registry methods ───────────────────────────────────────────────

    def _camera_add(self, data: Dict[str, Any]) -> None:
        """POST action=add_camera — add a new camera to the registry."""
        import uuid as _uuid
        try:
            from hc.json_manager import JSONManager
            from hc.models import CameraConfig, CAMERA_PROTOCOL_DEFAULTS
            name = str(data.get("name", "")).strip()
            if not name:
                raise ValueError("Camera name is required")
            protocol    = str(data.get("protocol", "rtsp")).strip()
            host        = str(data.get("host", "")).strip()
            if not host:
                raise ValueError("Host is required")
            source_type = str(data.get("source_type", "rtsp")).strip()
            # Default port based on protocol if not supplied
            try:
                port = int(data.get("port", 0))
            except (TypeError, ValueError):
                port = 0
            if port <= 0:
                port = CAMERA_PROTOCOL_DEFAULTS.get(protocol, 554)
            path     = str(data.get("path", "/")).strip() or "/"
            username = str(data.get("username", "")).strip()
            password = str(data.get("password", "")).strip()
            notes    = str(data.get("notes", "")).strip()
            enabled  = bool(data.get("enabled", True))
            cameras  = JSONManager.load_cameras()
            # Prevent duplicate names
            if any(c.name == name for c in cameras):
                raise ValueError(f"A camera named '{name}' already exists")
            cam = CameraConfig(
                camera_id   = str(_uuid.uuid4()),
                name        = name,
                protocol    = protocol,
                host        = host,
                port        = port,
                path        = path,
                username    = username,
                password    = password,
                source_type = source_type,
                enabled     = enabled,
                notes       = notes,
            )
            cameras.append(cam)
            JSONManager.save_cameras(cameras)
            log.info("Camera added: %s (%s)", cam.name, cam.url_masked)
            self._json({"ok": True, "msg": f"Camera '{name}' added.", "camera_id": cam.camera_id})
        except Exception as exc:
            self._json({"ok": False, "msg": str(exc)})

    def _camera_edit(self, data: Dict[str, Any]) -> None:
        """POST action=edit_camera — update an existing camera."""
        try:
            from hc.json_manager import JSONManager
            from hc.models import CAMERA_PROTOCOL_DEFAULTS
            camera_id = str(data.get("camera_id", "")).strip()
            if not camera_id:
                raise ValueError("camera_id is required")
            cameras = JSONManager.load_cameras()
            cam = next((c for c in cameras if c.camera_id == camera_id), None)
            if cam is None:
                raise ValueError(f"Camera '{camera_id}' not found")
            # Update only supplied fields
            if "name" in data:
                new_name = str(data["name"]).strip()
                if not new_name:
                    raise ValueError("Camera name cannot be empty")
                # Allow rename only if no collision with another camera
                if any(c.name == new_name and c.camera_id != camera_id for c in cameras):
                    raise ValueError(f"A camera named '{new_name}' already exists")
                cam.name = new_name
            if "protocol" in data:
                cam.protocol = str(data["protocol"]).strip()
            if "host" in data:
                h = str(data["host"]).strip()
                if not h:
                    raise ValueError("Host cannot be empty")
                cam.host = h
            if "port" in data:
                try:
                    p = int(data["port"])
                except (TypeError, ValueError):
                    p = 0
                if p <= 0:
                    p = CAMERA_PROTOCOL_DEFAULTS.get(cam.protocol, 554)
                cam.port = p
            if "path" in data:
                cam.path = str(data["path"]).strip() or "/"
            if "username" in data:
                cam.username = str(data["username"]).strip()
            # Password: preserve existing unless a new non-empty value is sent.
            # An explicit empty string clears the password (intentional reset).
            if "password" in data:
                new_pw = str(data["password"])
                if new_pw not in ("••••••••", ""):
                    # Real new password submitted
                    cam.password = new_pw
                elif new_pw == "" and data.get("clear_password"):
                    # Explicit clear requested via toggle
                    cam.password = ""
                # else: masked placeholder or absent — keep existing
            if "source_type" in data:
                cam.source_type = str(data["source_type"]).strip()
            if "enabled" in data:
                cam.enabled = bool(data["enabled"])
            if "notes" in data:
                cam.notes = str(data["notes"]).strip()
            JSONManager.save_cameras(cameras)
            log.info("Camera edited: %s (%s)", cam.name, cam.url_masked)
            self._json({"ok": True, "msg": f"Camera '{cam.name}' updated."})
        except Exception as exc:
            self._json({"ok": False, "msg": str(exc)})

    def _camera_delete(self, data: Dict[str, Any]) -> None:
        """POST action=delete_camera — remove a camera from the registry."""
        try:
            from hc.json_manager import JSONManager
            camera_id = str(data.get("camera_id", "")).strip()
            if not camera_id:
                raise ValueError("camera_id is required")
            cameras = JSONManager.load_cameras()
            before  = len(cameras)
            cameras = [c for c in cameras if c.camera_id != camera_id]
            if len(cameras) == before:
                raise ValueError(f"Camera '{camera_id}' not found")
            JSONManager.save_cameras(cameras)
            log.info("Camera deleted: %s", camera_id)
            self._json({"ok": True, "msg": "Camera deleted."})
        except Exception as exc:
            self._json({"ok": False, "msg": str(exc)})

    # ── Source switching ──────────────────────────────────────────────────────

    def _source_switch(self, data: Dict[str, Any]) -> None:
        """
        POST action=source_switch — flip active source between camera and playlist.

        Body: { name: "<stream name>", target: "camera" | "playlist" }
        """
        from hc.web import _WEB_MANAGER  # type: ignore
        try:
            name   = str(data.get("name", "")).strip()
            target = str(data.get("target", "")).strip()
            if not name:
                raise ValueError("Stream name is required")
            if target not in ("camera", "playlist"):
                raise ValueError("target must be 'camera' or 'playlist'")
            mgr = _WEB_MANAGER
            if not mgr:
                raise ValueError("Manager not ready")
            st = mgr.get_state(name)
            if not st:
                raise ValueError(f"Stream '{name}' not found")
            if st.config.source_mode == "playlist" and target == "camera":
                raise ValueError("Stream is not configured for camera/hybrid mode")
            # Delegate to manager — sets source_override, flips active_source, restarts worker
            mgr.switch_source(name, target, manual=True)
            log.info("source_switch: '%s' → %s (manual)", name, target)
            self._json({"ok": True, "msg": f"Switched '{name}' to {target}"})
        except Exception as exc:
            self._json({"ok": False, "msg": str(exc)})

    # ── Multipart upload ──────────────────────────────────────────────────────

    def _handle_upload(self) -> None:
        from hc.web import _invalidate_lib_cache, _notify_folder_upload  # type: ignore
        try:
            cl = int(self.headers.get("Content-Length", 0))
            if cl > UPLOAD_MAX_BYTES:
                self._json({"ok": False, "msg": "File exceeds 10 GB limit"}, 413)
                return
            ct = self.headers.get("Content-Type", "")
            boundary: Optional[bytes] = None
            for part in ct.split(";"):
                p = part.strip()
                if p.lower().startswith("boundary="):
                    boundary = p[9:].strip('"').encode("latin-1")
                    break
            if not boundary:
                self._json({"ok": False, "msg": "Missing boundary"})
                return
            raw = self.rfile.read(cl)
            sep = b"--" + boundary
            file_bytes: Optional[bytes] = None
            file_name:  Optional[str]   = None
            subdir = ""
            for seg in raw.split(sep):
                seg = seg.lstrip(b"\r\n")
                if not seg or seg.startswith(b"--"):
                    continue
                if b"\r\n\r\n" not in seg:
                    continue
                hdr_raw, body = seg.split(b"\r\n\r\n", 1)
                if body.endswith(b"\r\n"):
                    body = body[:-2]
                hdr_str = hdr_raw.decode("utf-8", errors="replace")
                cd_line = next(
                    (ln for ln in hdr_str.splitlines()
                     if ln.lower().startswith("content-disposition:")),
                    "",
                )
                field_name = fname = ""
                for tok in cd_line.split(";"):
                    tok = tok.strip()
                    if tok.startswith("name="):
                        field_name = tok[5:].strip('"')
                    elif tok.startswith("filename="):
                        fname = tok[9:].strip('"')
                if field_name == "file" and fname:
                    file_bytes = body
                    file_name  = fname
                elif field_name == "subdir":
                    subdir = body.decode("utf-8", errors="replace").strip().lstrip("/\\")
            if file_bytes is None or not file_name:
                self._json({"ok": False, "msg": "No file field found"})
                return
            subdir      = re.sub(r'[/\\<>"|?*\x00]', '_', subdir)[:128]
            subdir      = re.sub(r'\.\.', '_', subdir)
            fname_clean = Path(file_name).name
            ext         = Path(fname_clean).suffix.lower()
            if ext not in SUPPORTED_EXTS:
                self._json({"ok": False, "msg": f"Unsupported extension: {ext}"})
                return
            safe_name = re.sub(r'[^\w.\-]', '_', fname_clean)
            if not safe_name or safe_name.startswith('.'):
                self._json({"ok": False, "msg": "Invalid filename"})
                return
            dest_dir = (MEDIA_DIR() / subdir) if subdir else MEDIA_DIR()
            safe_dir = _safe_path(dest_dir, MEDIA_DIR())
            if safe_dir is None:
                self._json({"ok": False, "msg": "Invalid upload directory"})
                return
            safe_dir.mkdir(parents=True, exist_ok=True)
            dest     = safe_dir / safe_name
            tmp_path = dest.with_suffix(dest.suffix + ".tmp")
            try:
                tmp_path.write_bytes(file_bytes)
                tmp_path.rename(dest)
            except Exception:
                tmp_path.unlink(missing_ok=True)
                raise
            _invalidate_lib_cache()
            log.info("Upload saved: %s", dest)
            _notify_folder_upload(safe_dir)
            self._json({"ok": True, "msg": f"Saved: {safe_name}"})
        except Exception as exc:
            log.error("Upload error: %s", exc)
            self._json({"ok": False, "msg": f"Upload error: {exc}"}, 500)

    # ── Backup ────────────────────────────────────────────────────────────────

    def _handle_backup(self, include: Dict[str, Any]) -> None:
        import json as _json
        from hc.constants import BASE_DIR, CONFIG_DIR
        try:
            payload: Dict[str, Any] = {
                "format":  "hydracast_backup",
                "version": APP_VER,
                "created": datetime.now().isoformat(timespec="seconds"),
            }
            if include.get("streams", True):
                p = CONFIG_DIR() / "streams.json"
                try:
                    payload["streams"] = _json.loads(
                        p.read_text(encoding="utf-8")) if p.exists() else []
                except Exception:
                    payload["streams"] = []
            if include.get("events", True):
                p = CONFIG_DIR() / "events.json"
                try:
                    payload["events"] = _json.loads(
                        p.read_text(encoding="utf-8")) if p.exists() else []
                except Exception:
                    payload["events"] = []
            if include.get("mail", True):
                p = BASE_DIR() / "mail_config.json"
                try:
                    if p.exists():
                        mc = _json.loads(p.read_text(encoding="utf-8"))
                        mc.pop("password", None)
                        payload["mail_config"] = mc
                    else:
                        payload["mail_config"] = {}
                except Exception:
                    payload["mail_config"] = {}
            if include.get("resume", True):
                p = BASE_DIR() / "resume_positions.json"
                try:
                    payload["resume_positions"] = _json.loads(
                        p.read_text(encoding="utf-8")) if p.exists() else {}
                except Exception:
                    payload["resume_positions"] = {}
            # ── Camera registry (passwords stripped) ──────────────────────────
            if include.get("cameras", True):
                try:
                    from hc.json_manager import JSONManager
                    cameras = JSONManager.load_cameras()
                    payload["cameras"] = [
                        {
                            "camera_id":   c.camera_id,
                            "name":        c.name,
                            "protocol":    c.protocol,
                            "host":        c.host,
                            "port":        c.port,
                            "path":        c.path,
                            "username":    c.username,
                            # passwords are intentionally excluded from backups
                            "source_type": c.source_type,
                            "enabled":     c.enabled,
                            "notes":       c.notes,
                        }
                        for c in cameras
                    ]
                except Exception:
                    payload["cameras"] = []
            body  = _json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")
            ts    = datetime.now().strftime("%Y%m%d_%H%M%S")
            fname = f"hydracast_backup_{ts}.hc"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Disposition", f'attachment; filename="{fname}"')
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            from hc.web import _SEC_HEADERS  # type: ignore
            for k, v in _SEC_HEADERS.items():
                self.send_header(k, v)
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass
            log.info("Backup downloaded: %s (%d bytes)", fname, len(body))
        except Exception as exc:
            log.error("Backup error: %s", exc)
            self._json({"ok": False, "msg": f"Backup error: {exc}"}, 500)

    # ── Restore ───────────────────────────────────────────────────────────────

    def _handle_restore(self, payload: Dict[str, Any]) -> None:
        import json as _json
        from hc.constants import BASE_DIR, CONFIG_DIR
        from hc.web import _WEB_MANAGER  # type: ignore
        try:
            if payload.get("format") != "hydracast_backup":
                self._json({"ok": False, "msg": "Not a valid HydraCast backup file"})
                return
            restored: list = []
            if "streams" in payload:
                p = CONFIG_DIR() / "streams.json"
                p.write_text(
                    _json.dumps(payload["streams"], indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
                restored.append("streams")
            if "events" in payload:
                p = CONFIG_DIR() / "events.json"
                p.write_text(
                    _json.dumps(payload["events"], indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
                restored.append("events")
            if "mail_config" in payload:
                p = BASE_DIR() / "mail_config.json"
                existing: Dict[str, Any] = {}
                try:
                    if p.exists():
                        existing = _json.loads(p.read_text(encoding="utf-8"))
                except Exception:
                    pass
                mc = dict(payload["mail_config"])
                if "password" not in mc and "password" in existing:
                    mc["password"] = existing["password"]
                p.write_text(_json.dumps(mc, indent=4, ensure_ascii=False), encoding="utf-8")
                restored.append("mail_config")
            if "resume_positions" in payload:
                p = BASE_DIR() / "resume_positions.json"
                p.write_text(
                    _json.dumps(payload["resume_positions"], indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
                restored.append("resume_positions")
            # ── Camera registry ───────────────────────────────────────────────
            if "cameras" in payload and isinstance(payload["cameras"], list):
                try:
                    from hc.json_manager import JSONManager
                    from hc.models import CameraConfig as _CC
                    # Re-inject passwords from existing local cameras.hcf
                    existing_cams = {c.camera_id: c for c in JSONManager.load_cameras()}
                    merged: list = []
                    for entry in payload["cameras"]:
                        cam_id = entry.get("camera_id", "")
                        # Restore password from local store if not in backup
                        password = entry.get("password", "")
                        if not password and cam_id in existing_cams:
                            password = existing_cams[cam_id].password
                        merged.append(_CC(
                            camera_id   = cam_id,
                            name        = entry.get("name", ""),
                            protocol    = entry.get("protocol", "rtsp"),
                            host        = entry.get("host", ""),
                            port        = int(entry.get("port", 554)),
                            path        = entry.get("path", "/"),
                            username    = entry.get("username", ""),
                            password    = password,
                            source_type = entry.get("source_type", "rtsp"),
                            enabled     = bool(entry.get("enabled", True)),
                            notes       = entry.get("notes", ""),
                        ))
                    JSONManager.save_cameras(merged)
                    restored.append("cameras")
                except Exception as _ce:
                    log.warning("restore: cameras restore failed: %s", _ce)
            mgr = _WEB_MANAGER
            if mgr and "streams" in payload:
                try:
                    from hc.json_manager import JSONManager
                    new_configs = JSONManager.load()
                    mgr.reload_from_configs(new_configs)
                except AttributeError:
                    for st in list(mgr.states):
                        try:
                            mgr.restart(st.config.name)
                        except Exception:
                            pass
                except Exception as exc:
                    log.warning("restore: manager reload failed: %s", exc)
            if "events" in payload and mgr:
                try:
                    from hc.json_manager import JSONManager
                    mgr.events = JSONManager.load_events()
                except Exception:
                    pass
            self._json({
                "ok":      True,
                "msg":     f"Restored: {', '.join(restored)}. Streams reloaded.",
                "restored": restored,
            })
        except Exception as exc:
            log.error("Restore error: %s", exc)
            self._json({"ok": False, "msg": f"Restore error: {exc}"}, 500)

    # ── App Restart (Settings → Restart Application) ──────────────────────────

    def _handle_restart_process(self) -> None:
        """
        POST /api/action  { action: "restart_process" }

        Gracefully stops all streams, flushes HTTP 200, then terminates the
        entire process with os._exit(1) so the Guardian detects the exit and
        relaunches hydracast_bg.exe automatically.

        Why os._exit(1) instead of Popen + sys.exit:
        ─────────────────────────────────────────────
        When running inside hydracast_bg.exe the web handler runs on a worker
        thread.  sys.exit(0) only raises SystemExit which hydracast_bg catches
        and treats as a clean exit — the outer process stays alive and the
        Guardian never sees a crash.  subprocess.Popen([sys.executable] +
        sys.argv) doubles the exe path on a frozen build (e.g.
        "hydracast_bg.exe hydracast_bg.exe ...") and fails silently.

        os._exit(1) bypasses all Python cleanup and kills the process
        immediately.  Exit code 0 tells the Guardian this was an intentional
        restart (not a crash), and it relaunches the correct binary with the
        original arguments within its normal restart window.
        """
        import os as _os
        import time as _time
        import threading as _thr

        from hc.web import _WEB_MANAGER  # type: ignore

        mgr = _WEB_MANAGER

        # Stop all streams so FFmpeg/MediaMTX release their ports before the
        # new process tries to bind them.
        if mgr is not None:
            for st in list(getattr(mgr, "states", [])):
                try:
                    mgr.stop(st.config.name)
                except Exception:
                    pass

        self._json({"ok": True, "msg": "Restarting…"})

        def _do_restart() -> None:
            _time.sleep(0.8)   # let the HTTP response flush completely
            log.info("restart_process: calling os._exit(1) — Guardian will relaunch.")
            _os._exit(1)

        _thr.Thread(target=_do_restart, daemon=False,
                    name="hc-restart-process").start()

    # ── Factory Reset ─────────────────────────────────────────────────────────

    def _handle_reset(self, data: Dict[str, Any]) -> None:
        """
        POST /api/reset  –  Hard factory reset.

        Steps:
          1. Force-stop every running stream.
          2. Sleep briefly so the OS reclaims resources.
          3. Delete EVERYTHING inside config/.
          4. Clear in-memory state.
          5. Flush the HTTP response.
          6. os._exit(1) — kills the whole process so the Guardian detects
             the exit and relaunches hydracast_bg.exe cleanly.

        Why os._exit(1) instead of Popen + sys.exit:
        ─────────────────────────────────────────────
        This handler runs on a web-handler thread inside hydracast_bg.exe.
        sys.exit(0) raises SystemExit which hydracast_bg._run_hydracast_once()
        catches — the outer process stays alive and the Guardian never fires.
        subprocess.Popen([sys.executable] + sys.argv) doubles the exe path on
        a frozen build, spawning "hydracast_bg.exe hydracast_bg.exe ..." which
        fails silently.  os._exit(1) kills the process immediately; the
        Guardian detects exit-code 0, treats it as an intentional restart, and
        relaunches the correct binary with the original arguments.
        """
        import os as _os
        import sys as _sys
        import shutil as _shutil
        import threading as _thr
        import time as _time

        from hc.constants import CONFIG_DIR
        from hc.web import _WEB_MANAGER, _invalidate_lib_cache  # type: ignore

        if not data.get("confirm"):
            self._json({"ok": False, "msg": "confirm=true required"}, 400)
            return

        mgr = _WEB_MANAGER
        stopped: list = []
        errors:  list = []

        # ── 1. Force-stop all streams ─────────────────────────────────────────
        if mgr is not None:
            for st in list(getattr(mgr, "states", [])):
                try:
                    worker = mgr.get_worker(st.config.name)
                    if worker is not None:
                        try:
                            worker.kill()
                        except Exception:
                            pass
                    mgr.stop(st.config.name)
                    stopped.append(st.config.name)
                except Exception as exc:
                    errors.append(f"stop {st.config.name}: {exc}")

        _time.sleep(0.4)

        # ── 2. Wipe config/ ───────────────────────────────────────────────────
        cfg_dir = CONFIG_DIR()
        wiped: list = []
        try:
            for p in cfg_dir.iterdir():
                try:
                    if p.is_file() or p.is_symlink():
                        p.unlink()
                        wiped.append(p.name)
                    elif p.is_dir():
                        _shutil.rmtree(p, ignore_errors=False)
                        wiped.append(p.name + "/")
                except Exception as exc:
                    errors.append(f"delete {p.name}: {exc}")
                    log.error("reset: could not delete '%s': %s", p.name, exc)
        except Exception as exc:
            errors.append(f"config dir scan: {exc}")
            log.error("reset: config dir error: %s", exc)

        # ── 3. Clear in-memory state ──────────────────────────────────────────
        try:
            from hc.web_settings_manager import reset_settings
            reset_settings()
        except Exception as exc:
            errors.append(f"settings reset: {exc}")

        try:
            from hc.constants import set_media_roots
            set_media_roots([])
            _invalidate_lib_cache()
        except Exception:
            pass

        if mgr is not None:
            try:
                mgr.reload_from_configs([])
            except Exception:
                pass
            try:
                mgr.events = []
            except Exception:
                pass

        log.info("reset: wiped %d item(s): %s | stopped: %s%s",
                 len(wiped), ", ".join(wiped),
                 ", ".join(stopped) or "none",
                 f" | errors: {'; '.join(errors)}" if errors else "")

        # ── 4. Send response before the process exits ─────────────────────────
        self._json({
            "ok":      True,
            "msg":     "Reset complete — restarting…",
            "wiped":   wiped,
            "stopped": stopped,
            "errors":  errors,
        })

        # ── 5. Kill process so Guardian detects exit and relaunches ───────────
        def _exit() -> None:
            _time.sleep(0.8)   # let the HTTP response flush completely
            log.info("reset: calling os._exit(1) — Guardian will relaunch.")
            _os._exit(1)

        _thr.Thread(target=_exit, daemon=False, name="hc-reset-restart").start()
