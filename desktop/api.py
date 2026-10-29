# -*- coding: utf-8 -*-
"""pywebview JS API for the Fengyue desktop build.

The names mirror the methods the panel UI already calls on
`window.__cosai_ps_api`, so the same app.html can run inside Photoshop (UXP)
and inside this desktop shell.
"""

import base64
import ctypes
import datetime as _dt
import json
import os
import re
import time
import uuid
from pathlib import Path

import requests
import webview

from imaging import apply_local_filter, encode_png, feather_alpha, open_image
from ps_bridge import PSBridge

DATA_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "FengyueAI"
KEY_FILE = DATA_DIR / "cosai_key.json"
OUTPUT_FILE = DATA_DIR / "output.json"
LOG_DIR = DATA_DIR / "logs"
OUT_DIR = DATA_DIR / "output"
CHECKPOINT_DIR = DATA_DIR / "checkpoints"

APP_TITLE = "风月 AI"


def _ensure_dirs():
    for folder in (DATA_DIR, LOG_DIR, OUT_DIR, CHECKPOINT_DIR):
        folder.mkdir(parents=True, exist_ok=True)


def _read_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:  # noqa: BLE001
        return default


def _write_json(path, value):
    _ensure_dirs()
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(value, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _decode_b64(value):
    if not value:
        return b""
    raw = value
    if raw.lstrip().startswith("data:"):
        raw = raw.split(",", 1)[1]
    return base64.b64decode(raw)


def _safe_name(name, fallback="ai_gen"):
    base = os.path.basename(str(name or "").replace("\\", "/"))
    base = re.sub(r"[^\w\u4e00-\u9fff.\-() ]+", "_", base).strip()
    if not base:
        base = fallback + "_" + str(int(time.time())) + ".png"
    if not os.path.splitext(base)[1]:
        base += ".png"
    return base


class DesktopApi:
    def __init__(self):
        self._bridge = PSBridge()
        self._window = None
        _ensure_dirs()

    # ------------------------------------------------------------- window
    def _win(self):
        if self._window is not None:
            return self._window
        try:
            return webview.windows[0] if webview.windows else None
        except Exception:  # noqa: BLE001
            return None

    def win_minimize(self, payload=None):
        win = self._win()
        if win:
            win.minimize()
        return {"ok": True}

    def win_close(self, payload=None):
        win = self._win()
        if win:
            win.destroy()
        return {"ok": True}

    def win_toggle_max(self, payload=None):
        try:
            hwnd = ctypes.windll.user32.FindWindowW(None, APP_TITLE)
            if hwnd:
                zoomed = bool(ctypes.windll.user32.IsZoomed(hwnd))
                ctypes.windll.user32.ShowWindow(hwnd, 9 if zoomed else 3)
                return {"ok": True, "maximized": not zoomed}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}
        return {"ok": False}

    def open_folder(self, payload=None):
        path = (payload or {}).get("path") or str(DATA_DIR)
        try:
            os.startfile(path)  # noqa: S606 - expected desktop behaviour
            return {"ok": True}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    def app_info(self, payload=None):
        return {
            "mode": "desktop",
            "dataDir": str(DATA_DIR),
            "outputDir": str(OUT_DIR),
            "psConnected": self._bridge.connect(),
        }

    # ---------------------------------------------------------------- config
    def read_key_config(self, payload=None):
        return _read_json(KEY_FILE)

    def save_key_config(self, payload=None):
        cfg = (payload or {}).get("cfg") or {}
        _write_json(KEY_FILE, cfg)
        return {"success": True, "path": str(KEY_FILE)}

    def clear_key_config(self, payload=None):
        try:
            if KEY_FILE.exists():
                KEY_FILE.unlink()
        except OSError:
            pass
        return {"success": True}

    def _output_config(self):
        cfg = _read_json(OUTPUT_FILE, {}) or {}
        folder = cfg.get("folderPath") or ""
        return {
            "autoSave": bool(cfg.get("autoSave")),
            "folderPath": folder,
            "folderName": cfg.get("folderName") or (os.path.basename(folder.rstrip("\\/")) if folder else ""),
            "configured": bool(folder),
        }

    def get_output_config(self, payload=None):
        return self._output_config()

    def pick_output_folder(self, payload=None):
        win = self._win()
        if not win:
            return self._output_config()
        result = win.create_file_dialog(webview.FOLDER_DIALOG, directory=str(Path.home()))
        if not result:
            return self._output_config()
        folder = result[0] if isinstance(result, (list, tuple)) else result
        cfg = self._output_config()
        cfg["folderPath"] = str(folder)
        cfg["folderName"] = os.path.basename(str(folder).rstrip("\\/"))
        cfg["autoSave"] = True
        cfg["configured"] = True
        _write_json(OUTPUT_FILE, {
            "autoSave": True,
            "folderPath": cfg["folderPath"],
            "folderName": cfg["folderName"],
        })
        return cfg

    def save_output_config(self, payload=None):
        cfg = (payload or {}).get("cfg") or {}
        current = self._output_config()
        if "autoSave" in cfg:
            current["autoSave"] = bool(cfg["autoSave"])
        _write_json(OUTPUT_FILE, {
            "autoSave": current.get("autoSave", False),
            "folderPath": current.get("folderPath", ""),
            "folderName": current.get("folderName", ""),
        })
        return {"success": True, "config": self._output_config()}

    def clear_output_folder(self, payload=None):
        _write_json(OUTPUT_FILE, {"autoSave": False, "folderPath": "", "folderName": ""})
        return {"success": True, "config": self._output_config()}

    # ----------------------------------------------------------------- files
    def pick_image(self, payload=None):
        win = self._win()
        if not win:
            return None
        types = (
            "图片 (*.png;*.jpg;*.jpeg;*.webp;*.bmp;*.tif;*.tiff)",
            "所有文件 (*.*)",
        )
        result = win.create_file_dialog(webview.OPEN_DIALOG, allow_multiple=False, file_types=types)
        if not result:
            return None
        path = result[0] if isinstance(result, (list, tuple)) else result
        with open(path, "rb") as fh:
            raw = fh.read()
        img = open_image("data:image/png;base64," + base64.b64encode(raw).decode("ascii"))
        mime = "image/png"
        lower = path.lower()
        if lower.endswith((".jpg", ".jpeg")):
            mime = "image/jpeg"
        elif lower.endswith(".webp"):
            mime = "image/webp"
        elif lower.endswith(".bmp"):
            mime = "image/bmp"
        return {
            "name": os.path.basename(path),
            "path": path,
            "width": img.width,
            "height": img.height,
            "dataUrl": "data:" + mime + ";base64," + base64.b64encode(raw).decode("ascii"),
        }

    def _save_bytes(self, raw, file_name, use_output_folder=False):
        cfg = self._output_config()
        folder = cfg["folderPath"] if (use_output_folder and cfg.get("folderPath")) else str(OUT_DIR)
        Path(folder).mkdir(parents=True, exist_ok=True)
        path = os.path.join(folder, _safe_name(file_name))
        if os.path.exists(path):
            stem, ext = os.path.splitext(path)
            path = stem + "_" + uuid.uuid4().hex[:6] + ext
        with open(path, "wb") as fh:
            fh.write(raw)
        return {
            "path": path,
            "folder": folder,
            "fileName": os.path.basename(path),
            "outputFolder": bool(use_output_folder and cfg.get("folderPath")),
        }

    def save_image(self, payload=None):
        payload = payload or {}
        raw = _decode_b64(payload.get("base64"))
        saved = self._save_bytes(
            raw,
            payload.get("fileName") or ("ai_gen_" + str(int(time.time())) + ".png"),
            bool(payload.get("useOutputFolder")),
        )
        return saved

    def save_and_place(self, payload=None):
        payload = payload or {}
        raw = _decode_b64(payload.get("base64"))
        feather = float(payload.get("feather") or 0)
        if feather > 0:
            raw = feather_alpha(raw, feather)
        saved = self._save_bytes(
            raw,
            payload.get("fileName") or ("ai_gen_" + str(int(time.time())) + ".png"),
            bool(payload.get("useOutputFolder")),
        )
        out = {"saved": saved, "placed": False, "layer": None, "placeError": None}
        if not self._bridge.connect():
            out["placeError"] = "未桥接 Photoshop（独立模式）"
            return out
        try:
            layer = self._bridge.place_image(
                "data:image/png;base64," + base64.b64encode(raw).decode("ascii"),
                payload.get("layerName") or "AI 生成结果",
            )
            out["placed"] = True
            out["layer"] = layer
            if payload.get("group", True):
                try:
                    group = self._bridge.group_layer(layer["id"], payload.get("groupName") or "风月 AI")
                    out["group"] = group
                except Exception as exc:  # noqa: BLE001
                    out["groupError"] = str(exc)
        except Exception as exc:  # noqa: BLE001
            out["placeError"] = str(exc)
        return out

    def place_image(self, payload=None):
        payload = payload or {}
        if not self._bridge.connect():
            raise RuntimeError("未桥接 Photoshop（独立模式）")
        return self._bridge.place_image(payload.get("base64"), payload.get("name") or "AI 生成结果")

    # ------------------------------------------------------------------ http
    def http_request(self, payload=None):
        payload = payload or {}
        spec = payload.get("spec") or {}
        timeout_ms = int(payload.get("timeoutMs") or 60000)
        url = spec.get("url")
        if not url:
            raise RuntimeError("httpRequest 缺少 url")
        method = (spec.get("method") or "GET").upper()
        headers = spec.get("headers") or {}
        data = None
        files = None
        form = spec.get("form")
        if form:
            fields = form.get("fields") or {}
            data = {k: str(v) for k, v in fields.items()}
            files = []
            for item in (form.get("files") or []):
                files.append((
                    item.get("name") or "file",
                    (
                        item.get("filename") or "image.png",
                        _decode_b64(item.get("base64")),
                        item.get("mime") or "application/octet-stream",
                    ),
                ))
        elif spec.get("body") is not None:
            body = spec["body"]
            data = body.encode("utf-8") if isinstance(body, str) else body
        timeout = max(1.0, timeout_ms / 1000.0)
        resp = requests.request(
            method, url, headers=headers, data=data, files=files,
            timeout=(10, timeout),
        )
        body_mime = resp.headers.get("content-type", "")
        out = {
            "ok": bool(resp.ok),
            "status": int(resp.status_code),
            "bodyMime": body_mime,
            "bodyText": resp.text,
        }
        if spec.get("responseType") == "base64":
            out["bodyBase64"] = base64.b64encode(resp.content).decode("ascii")
            out.pop("bodyText", None)
        return out

    # ------------------------------------------------------------------ logs
    def append_log(self, payload=None):
        entries = (payload or {}).get("entries") or []
        _ensure_dirs()
        day = _dt.date.today().strftime("%Y-%m-%d")
        path = LOG_DIR / (day + ".jsonl")
        with open(path, "a", encoding="utf-8") as fh:
            for entry in entries:
                row = dict(entry or {})
                row.setdefault("ts", _dt.datetime.now().isoformat(timespec="milliseconds"))
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        return {"success": True, "path": str(path)}

    def list_logs(self, payload=None):
        _ensure_dirs()
        files = []
        for item in sorted(LOG_DIR.glob("*.jsonl"), reverse=True)[:30]:
            files.append({"name": item.name, "size": item.stat().st_size})
        return {"success": True, "files": files, "dir": str(LOG_DIR)}

    def clear_logs(self, payload=None):
        day = (payload or {}).get("dateStr") or _dt.date.today().strftime("%Y-%m-%d")
        path = LOG_DIR / (day + ".jsonl")
        try:
            if path.exists():
                path.unlink()
        except OSError:
            pass
        return {"success": True}

    # ----------------------------------------------------------------- local
    def local_filter(self, payload=None):
        payload = payload or {}
        try:
            src = payload.get("base64") or ""
            print("[LocalFilter] kind=%s len=%d" % (payload.get("kind") or "freq", len(src)), flush=True)
            out = apply_local_filter(
                payload.get("kind") or "freq",
                src,
                payload.get("params") or {},
            )
            print("[LocalFilter] ok len=%d" % len(out.get("base64") or ""), flush=True)
            return out
        except Exception as exc:  # noqa: BLE001
            print("[LocalFilter] error:", exc, flush=True)
            raise

    # -------------------------------------------------------------------- PS
    def ps_status(self, payload=None):
        return self._bridge.status()

    def ps_connect(self, payload=None):
        self._bridge.connect()
        return self._bridge.status()

    def ps_disconnect(self, payload=None):
        return self._bridge.disconnect()

    def ps_launch(self, payload=None):
        return self._bridge.launch()

    def ps_export_active(self, payload=None):
        try:
            max_edge = int((payload or {}).get("maxEdge") or 1600)
            return self._bridge.export_active(max_edge)
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)}

    def ps_get_document(self, payload=None):
        try:
            return {"ok": True, "doc": self._bridge.doc_info()}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    def ps_list_documents(self, payload=None):
        try:
            raw = self._bridge.jsx(
                "(function(){var a=[];for(var i=0;i<app.documents.length;i++){"
                "a.push(app.documents[i].id + String.fromCharCode(1) + app.documents[i].name);}"
                "return a.join(String.fromCharCode(2));})()"
            ) or ""
            rows = []
            for item in str(raw).split("\x02"):
                parts = item.split("\x01")
                if len(parts) >= 2:
                    rows.append({"id": parts[0], "name": parts[1]})
            return {"ok": True, "documents": rows}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    def ps_list_layers(self, payload=None):
        try:
            return {"ok": True, "layers": self._bridge.list_layers()}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    def ps_selection_bounds(self, payload=None):
        try:
            return {"ok": True, "bounds": self._bridge.selection_bounds()}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    def ps_undo(self, payload=None):
        return self._bridge.undo()

    def ps_redo(self, payload=None):
        return self._bridge.redo()

    def ps_stamp_visible(self, payload=None):
        return self._bridge.stamp_visible()

    def ps_merge_visible(self, payload=None):
        return self._bridge.merge_visible()

    # ------------------------------------------------------- A 组快捷动作
    def ps_solo_layer(self, payload=None):
        try:
            return {"ok": True, **self._bridge.solo_layer()}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    def ps_restore_layers(self, payload=None):
        try:
            return {"ok": True, **self._bridge.restore_layers()}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    def ps_export_checkpoint(self, payload=None):
        try:
            _ensure_dirs()
            return {"ok": True, **self._bridge.export_checkpoint(str(CHECKPOINT_DIR))}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    def ps_save_selection(self, payload=None):
        try:
            name = (payload or {}).get("name") or "CosAI 快照"
            return {"ok": True, **self._bridge.save_selection(name)}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    def ps_load_selection(self, payload=None):
        try:
            name = (payload or {}).get("name") or "CosAI 快照"
            return {"ok": True, **self._bridge.load_selection(name)}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}
