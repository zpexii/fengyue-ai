# -*- coding: utf-8 -*-
"""Photoshop COM bridge for the desktop build.

Everything is optional: if Photoshop is not running, the desktop UI keeps
working in standalone mode. All bridge calls are guarded so a missing host
never crashes the app.
"""

import base64
import binascii
import json
import os
import tempfile
import time
import uuid


def _js_str(value):
    """JSON-escape a value for embedding inside a JSX string literal."""
    return json.dumps(str(value), ensure_ascii=False)


def _com_init():
    """pywebview calls the API from a worker thread: COM must be initialized per thread."""
    try:
        import pythoncom
        pythoncom.CoInitialize()
    except Exception:  # noqa: BLE001
        pass


class PSBridge:
    def __init__(self):
        self._app = None
        self._last_error = None

    # ------------------------------------------------------------------ base
    def connect(self):
        try:
            _com_init()
            import win32com.client
            # Re-bind on the current thread: a COM proxy cached in another thread
            # cannot be marshalled safely, and Photoshop may have restarted.
            try:
                app = win32com.client.GetActiveObject("Photoshop.Application")
            except Exception:  # noqa: BLE001 - not running yet: launch it
                app = win32com.client.Dispatch("Photoshop.Application")
            _ = app.Version
            self._app = app
            self._last_error = None
            return True
        except Exception as exc:  # noqa: BLE001 - host errors are open-ended
            self._app = None
            self._last_error = str(exc)
            return False

    def disconnect(self):
        self._app = None
        self._last_error = None
        return {"connected": False}

    def status(self):
        """Attach to a running Photoshop only; never launch it on startup."""
        try:
            _com_init()
            import win32com.client
            app = win32com.client.GetActiveObject("Photoshop.Application")
            _ = app.Version
            self._app = app
            self._last_error = None
        except Exception as exc:  # noqa: BLE001
            self._last_error = str(exc)
            return {"connected": False, "error": str(exc) or "Photoshop 未运行"}
        out = {"connected": True, "version": str(self._app.Version)}
        try:
            out["doc"] = self.doc_info()
        except Exception as exc:  # noqa: BLE001
            out["doc"] = None
            out["error"] = str(exc)
        return out

    def jsx(self, code):
        if not self.connect():
            raise RuntimeError(self._last_error or "Photoshop 未连接")
        return self._app.DoJavaScript(code, [], 1)

    # ------------------------------------------------------------------ info
    def doc_info(self):
        jsx = r"""
(function(){
  if (!app.documents.length) return '';
  var d = app.activeDocument;
  return [
    d.name,
    Math.round(d.width.as('px')),
    Math.round(d.height.as('px')),
    Math.round(d.resolution),
    String(d.mode),
    d.layers.length
  ].join(String.fromCharCode(1));
})()
"""
        raw = self.jsx(jsx)
        if not raw:
            return None
        parts = str(raw).split("\x01")
        if len(parts) < 6:
            return {"name": str(raw)}
        return {
            "name": parts[0],
            "width": int(float(parts[1])),
            "height": int(float(parts[2])),
            "resolution": int(float(parts[3])),
            "mode": parts[4],
            "layers": int(float(parts[5])),
        }

    def selection_bounds(self):
        jsx = r"""
(function(){
  try {
    var b = app.activeDocument.selection.bounds;
    if (!b) return '';
    return [b[0].as('px'), b[1].as('px'), b[2].as('px'), b[3].as('px')].join(',');
  } catch (e) { return ''; }
})()
"""
        raw = self.jsx(jsx)
        if not raw:
            return None
        try:
            left, top, right, bottom = [int(float(x)) for x in str(raw).split(",")[:4]]
            return {"left": left, "top": top, "right": right, "bottom": bottom}
        except Exception:  # noqa: BLE001
            return None

    def list_layers(self):
        jsx = r"""
(function(){
  function walk(container, depth, rows){
    for (var i = 0; i < container.layers.length; i++) {
      var l = container.layers[i];
      rows.push([l.id, l.name, l.visible, String(l.typename), depth].join(String.fromCharCode(1)));
      if (String(l.typename) === 'LayerSet') walk(l, depth + 1, rows);
    }
  }
  if (!app.documents.length) return '';
  var rows = [];
  walk(app.activeDocument, 0, rows);
  return rows.join(String.fromCharCode(2));
})()
"""
        raw = self.jsx(jsx)
        if not raw:
            return []
        out = []
        for row in str(raw).split("\x02"):
            parts = row.split("\x01")
            if len(parts) < 5:
                continue
            out.append({
                "id": parts[0],
                "name": parts[1],
                "visible": parts[2] == "true",
                "kind": parts[3],
                "depth": int(float(parts[4])),
            })
        return out

    def select_layer(self, layer_id):
        jsx = r"""
(function(){
  var target = app.activeDocument;
  function find(container){
    for (var i = 0; i < container.layers.length; i++) {
      var l = container.layers[i];
      if (String(l.id) === %s) return l;
      if (String(l.typename) === 'LayerSet') { var hit = find(l); if (hit) return hit; }
    }
    return null;
  }
  var l = find(target);
  if (!l) return '';
  target.activeLayer = l;
  return String(l.id);
})()
""" % _js_str(layer_id)
        return self.jsx(jsx)

    # --------------------------------------------------------------- capture
    def export_active(self, max_edge=1600):
        temp = os.path.join(tempfile.gettempdir(), "cosai_ps_export_" + uuid.uuid4().hex + ".png")
        jsx = r"""
(function(){
  if (!app.documents.length) return '';
  app.displayDialogs = DialogModes.NO;
  var src = app.activeDocument;
  var d = null;
  var tempName = '';
  try {
    d = src.duplicate('cosai_capture_tmp');
    tempName = d.name;
    var maxEdge = %d;
    var w = d.width.as('px'), h = d.height.as('px');
    var scale = Math.min(1, maxEdge / Math.max(w, h));
    if (scale < 1) d.resizeImage(UnitValue(w * scale, 'px'), UnitValue(h * scale, 'px'), 72, ResampleMethod.BICUBIC);
    var f = new File(%s);
    var o = new PNGSaveOptions();
    o.compression = 6;
    d.saveAs(f, o, true, Extension.LOWERCASE);
    d.close(SaveOptions.DONOTSAVECHANGES);
    app.activeDocument = src;
    return tempName;
  } catch (e) {
    try { if (d) d.close(SaveOptions.DONOTSAVECHANGES); } catch (_) {}
    try { app.activeDocument = src; } catch (_) {}
    return 'ERR:' + e.message;
  }
})()
""" % (int(max_edge), _js_str(temp.replace("\\", "/")))
        raw = self.jsx(jsx) or ""
        if raw.startswith("ERR:"):
            raise RuntimeError(raw[4:])
        if not os.path.isfile(temp):
            raise RuntimeError("Photoshop 未返回截图文件")
        with open(temp, "rb") as fh:
            data = fh.read()
        try:
            os.remove(temp)
        except OSError:
            pass
        return {"base64": base64.b64encode(data).decode("ascii"), "mime": "image/png"}

    # ---------------------------------------------------------------- place
    def _write_temp_png(self, b64):
        if "," in b64 and b64.lstrip().startswith("data:"):
            b64 = b64.split(",", 1)[1]
        try:
            raw = base64.b64decode(b64)
        except (binascii.Error, ValueError) as exc:
            raise RuntimeError("图片数据无效: %s" % exc)
        path = os.path.join(tempfile.gettempdir(), "cosai_place_" + uuid.uuid4().hex + ".png")
        with open(path, "wb") as fh:
            fh.write(raw)
        return path

    def place_image(self, base64_data, name="AI 生成结果"):
        path = self._write_temp_png(base64_data)
        jsx = r"""
(function(){
  if (!app.documents.length) return 'ERR:没有打开的文档';
  app.displayDialogs = DialogModes.NO;
  var target = app.activeDocument;
  var src = null;
  try {
    var f = new File(%s);
    src = app.open(f);
    src.selection.selectAll();
    src.selection.copy();
    src.close(SaveOptions.DONOTSAVECHANGES);
    app.activeDocument = target;
    target.paste();
    var l = target.activeLayer;
    l.name = %s;
    return String(l.id);
  } catch (e) {
    try { if (src) src.close(SaveOptions.DONOTSAVECHANGES); } catch (_) {}
    return 'ERR:' + e.message;
  }
})()
""" % (_js_str(path.replace("\\", "/")), _js_str(name))
        raw = self.jsx(jsx) or ""
        try:
            os.remove(path)
        except OSError:
            pass
        if raw.startswith("ERR:"):
            raise RuntimeError(raw[4:])
        return {"id": raw, "name": name}

    def group_layer(self, layer_id, group_name="风月 AI"):
        jsx = r"""
(function(){
  var d = app.activeDocument;
  function find(container){
    for (var i = 0; i < container.layers.length; i++) {
      var l = container.layers[i];
      if (String(l.id) === %s) return l;
      if (String(l.typename) === 'LayerSet') { var hit = find(l); if (hit) return hit; }
    }
    return null;
  }
  var l = find(d);
  if (!l) return 'ERR:找不到图层';
  try {
    var name = %s;
    var g = null;
    for (var i = 0; i < d.layerSets.length; i++) {
      if (d.layerSets[i].name === name) { g = d.layerSets[i]; break; }
    }
    if (!g) { g = d.layerSets.add(); g.name = name; }
    l.move(g, ElementPlacement.INSIDE);
    return String(g.id);
  } catch (e) { return 'ERR:' + e.message; }
})()
""" % (_js_str(layer_id), _js_str(group_name))
        raw = self.jsx(jsx) or ""
        if raw.startswith("ERR:"):
            raise RuntimeError(raw[4:])
        return {"id": raw, "name": group_name}

    # ------------------------------------------------------------- history
    def undo(self):
        jsx = r"""
(function(){
  var d = app.activeDocument;
  var states = d.historyStates;
  var active = d.activeHistoryState;
  var idx = -1;
  for (var i = 0; i < states.length; i++) { if (states[i] === active) { idx = i; break; } }
  if (idx <= 0) return 'ERR:没有可撤销的操作';
  d.activeHistoryState = states[idx - 1];
  return 'OK';
})()
"""
        raw = self.jsx(jsx) or ""
        if raw.startswith("ERR:"):
            raise RuntimeError(raw[4:])
        return {"success": True}

    def redo(self):
        jsx = r"""
(function(){
  var d = app.activeDocument;
  var states = d.historyStates;
  var active = d.activeHistoryState;
  var idx = -1;
  for (var i = 0; i < states.length; i++) { if (states[i] === active) { idx = i; break; } }
  if (idx < 0 || idx >= states.length - 1) return 'ERR:没有可重做的操作';
  d.activeHistoryState = states[idx + 1];
  return 'OK';
})()
"""
        raw = self.jsx(jsx) or ""
        if raw.startswith("ERR:"):
            raise RuntimeError(raw[4:])
        return {"success": True}

    def stamp_visible(self):
        jsx = r"""
(function(){
  if (!app.documents.length) return 'ERR:没有打开的文档';
  app.displayDialogs = DialogModes.NO;
  try {
    var desc = new ActionDescriptor();
    desc.putBoolean(charIDToTypeID('Dplc'), true);
    executeAction(charIDToTypeID('MrgV'), desc, DialogModes.NO);
    return 'OK';
  } catch (e) { return 'ERR:' + e.message; }
})()
"""
        raw = self.jsx(jsx) or ""
        if raw.startswith("ERR:"):
            raise RuntimeError(raw[4:])
        return {"success": True}

    def merge_visible(self):
        jsx = r"""
(function(){
  if (!app.documents.length) return 'ERR:没有打开的文档';
  app.displayDialogs = DialogModes.NO;
  try { app.activeDocument.mergeVisibleLayers(); return 'OK'; }
  catch (e) { return 'ERR:' + e.message; }
})()
"""
        raw = self.jsx(jsx) or ""
        if raw.startswith("ERR:"):
            raise RuntimeError(raw[4:])
        return {"success": True}

    # ------------------------------------------------------- A组快捷动作
    def _solo_snapshot_path(self):
        return os.path.join(tempfile.gettempdir(), "cosai_solo_snapshot.txt")

    def solo_layer(self):
        snapshot_path = self._solo_snapshot_path()
        jsx = r"""
(function(){
  if (!app.documents.length) return 'ERR:没有打开的文档';
  var d = app.activeDocument;
  var active = d.activeLayer;
  var snap = [];
  function walk(c){
    for (var i = 0; i < c.layers.length; i++) {
      var l = c.layers[i];
      snap.push([String(l.id), l.visible]);
      if (String(l.typename) === 'LayerSet') walk(l);
    }
  }
  function contains(c, id){
    for (var i = 0; i < c.layers.length; i++) {
      var l = c.layers[i];
      if (String(l.id) === id) return true;
      if (String(l.typename) === 'LayerSet' && contains(l, id)) return true;
    }
    return false;
  }
  function apply(c){
    for (var i = 0; i < c.layers.length; i++) {
      var l = c.layers[i];
      var keep = String(l.id) === String(active.id) || (String(l.typename) === 'LayerSet' && contains(l, String(active.id)));
      l.visible = keep;
      if (String(l.typename) === 'LayerSet') apply(l);
    }
  }
  try {
    walk(d);
    try {
      var f = new File(%s);
      f.encoding = 'UTF8';
      f.open('w');
      f.writeln(String(d.id));
      for (var k = 0; k < snap.length; k++) f.writeln(snap[k][0] + '\t' + (snap[k][1] ? '1' : '0'));
      f.close();
    } catch (fe) {}
    apply(d);
    return active.name;
  } catch (e) { return 'ERR:' + e.message; }
})()
""" % _js_str(snapshot_path.replace("\\", "/"))
        raw = self.jsx(jsx) or ""
        if raw.startswith("ERR:"):
            raise RuntimeError(raw[4:])
        return {"success": True, "name": raw}

    def restore_layers(self):
        snapshot_path = self._solo_snapshot_path()
        jsx = r"""
(function(){
  if (!app.documents.length) return 'ERR:没有打开的文档';
  var d = app.activeDocument;
  var f = new File(%s);
  if (!f.exists) return 'ERR:没有独显记录';
  f.encoding = 'UTF8';
  f.open('r');
  var docId = String(f.readln());
  if (docId !== String(d.id)) { f.close(); return 'ERR:独显记录属于其他文档'; }
  function find(c, id){
    for (var i = 0; i < c.layers.length; i++) {
      var l = c.layers[i];
      if (String(l.id) === id) return l;
      if (String(l.typename) === 'LayerSet') { var hit = find(l, id); if (hit) return hit; }
    }
    return null;
  }
  var n = 0;
  while (!f.eof) {
    var line = String(f.readln());
    var parts = line.split('\t');
    if (parts.length < 2) continue;
    var l = find(d, parts[0]);
    if (l) { try { l.visible = (parts[1] === '1'); n++; } catch (e) {} }
  }
  f.close();
  try { f.remove(); } catch (e) {}
  return String(n);
})()
""" % _js_str(snapshot_path.replace("\\", "/"))
        raw = self.jsx(jsx) or ""
        if raw.startswith("ERR:"):
            raise RuntimeError(raw[4:])
        return {"success": True, "restored": int(float(raw or 0))}

    def export_checkpoint(self, folder_path):
        os.makedirs(folder_path, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S") + "-%03d" % int((time.time() % 1) * 1000)
        path = os.path.join(folder_path, "checkpoint_%s.png" % stamp)
        jsx = r"""
(function(){
  if (!app.documents.length) return 'ERR:没有打开的文档';
  app.displayDialogs = DialogModes.NO;
  var src = app.activeDocument;
  var d = null;
  try {
    d = src.duplicate('cosai_checkpoint_tmp');
    var f = new File(%s);
    var o = new PNGSaveOptions();
    o.compression = 6;
    d.saveAs(f, o, true, Extension.LOWERCASE);
    d.close(SaveOptions.DONOTSAVECHANGES);
    app.activeDocument = src;
    return 'OK';
  } catch (e) {
    try { if (d) d.close(SaveOptions.DONOTSAVECHANGES); } catch (_) {}
    try { app.activeDocument = src; } catch (_) {}
    return 'ERR:' + e.message;
  }
})()
""" % _js_str(path.replace("\\", "/"))
        raw = self.jsx(jsx) or ""
        if raw.startswith("ERR:"):
            raise RuntimeError(raw[4:])
        return {"success": True, "path": path, "fileName": os.path.basename(path)}

    def save_selection(self, name="CosAI 快照"):
        jsx = r"""
(function(){
  if (!app.documents.length) return 'ERR:没有打开的文档';
  var d = app.activeDocument;
  try { var b = d.selection.bounds; } catch (e) { return 'ERR:当前没有选区'; }
  var name = %s;
  var ch = null;
  for (var i = 0; i < d.channels.length; i++) {
    if (d.channels[i].name === name) { ch = d.channels[i]; break; }
  }
  try {
    if (!ch) { ch = d.channels.add(); ch.name = name; }
    d.selection.store(ch);
    return name;
  } catch (e) { return 'ERR:' + e.message; }
})()
""" % _js_str(name)
        raw = self.jsx(jsx) or ""
        if raw.startswith("ERR:"):
            raise RuntimeError(raw[4:])
        return {"success": True, "name": raw}

    def load_selection(self, name="CosAI 快照"):
        jsx = r"""
(function(){
  if (!app.documents.length) return 'ERR:没有打开的文档';
  var d = app.activeDocument;
  var name = %s;
  var ch = null;
  for (var i = 0; i < d.channels.length; i++) {
    if (d.channels[i].name === name) { ch = d.channels[i]; break; }
  }
  if (!ch) return 'ERR:没有找到选区快照：' + name;
  try {
    d.selection.load(ch, SelectionType.REPLACE);
    return name;
  } catch (e) { return 'ERR:' + e.message; }
})()
""" % _js_str(name)
        raw = self.jsx(jsx) or ""
        if raw.startswith("ERR:"):
            raise RuntimeError(raw[4:])
        return {"success": True, "name": raw}

    def launch(self):
        """Dispatch starts Photoshop when it is not running (may take a while)."""
        try:
            _com_init()
            import win32com.client
            app = win32com.client.Dispatch("Photoshop.Application")
            _ = app.Version
            self._app = app
            return {"connected": True, "version": str(app.Version)}
        except Exception as exc:  # noqa: BLE001
            return {"connected": False, "error": str(exc)}
