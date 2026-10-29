# -*- coding: utf-8 -*-
"""Fengyue AI desktop shell.

Run:  python desktop/main.py
"""

import ctypes
import multiprocessing
import os
import sys
import tempfile
import traceback
from datetime import datetime
from pathlib import Path

import webview

from api import APP_TITLE, DesktopApi

ROOT = Path(sys._MEIPASS) if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent
APP_HTML = ROOT / "app.html"
APP_ICON = ROOT / "desktop" / "assets" / "fengyue.ico"

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:  # noqa: BLE001
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def _boot(msg):
    try:
        base = Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "FengyueAI"
        base.mkdir(parents=True, exist_ok=True)
        with open(base / "boot.log", "a", encoding="utf-8") as fh:
            fh.write(datetime.now().isoformat(timespec="seconds") + " " + msg + "\n")
    except Exception:  # noqa: BLE001
        pass


def main():
    _boot("enter")
    if not APP_HTML.is_file():
        raise SystemExit("找不到界面文件: %s" % APP_HTML)

    api = DesktopApi()
    _boot("api ready")
    window = webview.create_window(
        APP_TITLE,
        str(APP_HTML),
        js_api=api,
        width=1320,
        height=860,
        min_size=(1024, 680),
        frameless=True,
        easy_drag=True,
        background_color="#071524",
        text_select=True,
    )
    api._window = window
    _boot("window created")

    panel = ""
    demo = ""
    for arg in sys.argv:
        if arg.startswith("--panel="):
            panel = arg.split("=", 1)[1]
        elif arg.startswith("--demo="):
            demo = arg.split("=", 1)[1]

    def _on_loaded():
        try:
            if panel in ("home", "draw", "presets", "config", "tools", "logs"):
                window.evaluate_js(
                    "setTimeout(function(){window.__cosaiSwitchPanel && window.__cosaiSwitchPanel('%s');}, 400)" % panel
                )
            if "--probe" in sys.argv:
                import threading
                import time as _time

                def _probe():
                    _time.sleep(6)
                    try:
                        info = window.evaluate_js(
                            "JSON.stringify(window.__cosaiDebugState && window.__cosaiDebugState())"
                        )
                        print("[Desktop] state:", info, flush=True)
                    except Exception as exc:  # noqa: BLE001
                        print("[Desktop] state error:", exc, flush=True)

                threading.Thread(target=_probe, daemon=True).start()
            if demo == "localfreq":
                window.evaluate_js(
                    "setTimeout(function(){var el=document.getElementById('cosai-tool-freq'); if(el) el.click();}, 5000)"
                )
            elif demo == "localfreqhome":
                window.evaluate_js(
                    "setTimeout(function(){var el=document.getElementById('cosai-tool-freq'); if(el) el.click();}, 5000);"
                    "setTimeout(function(){window.__cosaiSwitchPanel && window.__cosaiSwitchPanel('home');}, 9000)"
                )
            elif demo == "dropdown":
                window.evaluate_js(
                    "setTimeout(function(){var t=document.querySelectorAll('.cosai-dd-trigger')[1]; if(t) t.click();}, 900)"
                )
            elif demo == "presetmodal":
                window.evaluate_js(
                    "setTimeout(function(){var b=document.getElementById('cosai-preset-add'); if(b) b.click();}, 900)"
                )
            elif demo == "presetapply":
                window.evaluate_js(
                    "setTimeout(function(){var c=document.querySelector('.cosai-preset-card'); if(c) c.click();}, 900)"
                )
            elif demo == "psoff":
                window.evaluate_js(
                    "setTimeout(function(){var el=document.getElementById('cosai-ps-toggle'); if(el) el.click();}, 3000)"
                )
            elif demo == "pson":
                window.evaluate_js(
                    "setTimeout(function(){var el=document.getElementById('cosai-ps-toggle'); if(el) el.click();}, 3000)"
                )
            info = window.evaluate_js(
                "({w: window.innerWidth, h: window.innerHeight, dpr: window.devicePixelRatio,"
                " sw: screen.width, sh: screen.height, avail: screen.availHeight})"
            )
            print("[Desktop] viewport:", info, flush=True)
        except Exception as exc:  # noqa: BLE001
            print("[Desktop] viewport error:", exc, flush=True)

    window.events.loaded += _on_loaded
    _boot("starting webview")
    webview.start(
        debug=("--debug" in sys.argv),
        icon=str(APP_ICON) if APP_ICON.is_file() else None,
    )
    _boot("webview returned")


def _log_crash():
    try:
        base = Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "FengyueAI"
        base.mkdir(parents=True, exist_ok=True)
        with open(base / "error.log", "a", encoding="utf-8") as fh:
            fh.write(datetime.now().isoformat(timespec="seconds") + "\n")
            fh.write(traceback.format_exc() + "\n")
    except Exception:  # noqa: BLE001
        pass


if __name__ == "__main__":
    # Windowed PyInstaller builds have no console: some libraries write to
    # stdout/stderr and crash silently when they are None.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    multiprocessing.freeze_support()
    try:
        main()
    except BaseException:  # noqa: BLE001 - windowed build has no console
        _log_crash()
        raise
