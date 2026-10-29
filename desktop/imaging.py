# -*- coding: utf-8 -*-
"""Local image processing used by the standalone desktop mode.

These are intentionally simple, dependency-light algorithms (Pillow + numpy)
so the app keeps working with no Photoshop and no local AI runtime.
"""

import base64
import io

import numpy as np
from PIL import Image, ImageFilter


def decode_data_url(value):
    if not value:
        raise ValueError("没有图片数据")
    raw = value
    if raw.lstrip().startswith("data:"):
        raw = raw.split(",", 1)[1]
    try:
        data = base64.b64decode(raw)
    except Exception as exc:  # noqa: BLE001
        raise ValueError("图片 base64 无效: %s" % exc)
    return data


def encode_png(img):
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def open_image(value):
    return Image.open(io.BytesIO(decode_data_url(value)))


def _clip(arr):
    return np.clip(arr, 0, 255).astype(np.uint8)


def feather_alpha(data, radius=8.0):
    """Soften the alpha edge of a PNG before it is placed on the canvas."""
    if radius <= 0:
        return data
    img = Image.open(io.BytesIO(data)).convert("RGBA")
    alpha = img.getchannel("A")
    img.putalpha(alpha.filter(ImageFilter.GaussianBlur(float(radius))))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def frequency_separation(img, radius=8.0, texture=0.55):
    """High/Low frequency split: keep colour, dial texture down or up."""
    base = img.convert("RGB")
    arr = np.asarray(base).astype(np.float32)
    low = np.asarray(base.filter(ImageFilter.GaussianBlur(float(radius)))).astype(np.float32)
    high = arr - low + 128.0
    out = low + (high - 128.0) * float(texture)
    return Image.fromarray(_clip(out), "RGB")


def glow(img, threshold=190, radius=18, strength=0.55):
    """Bloom: screen-blend a blurred copy of the bright areas."""
    base = img.convert("RGB")
    arr = np.asarray(base).astype(np.float32) / 255.0
    t = max(1, min(254, int(threshold))) / 255.0
    lum = arr.max(axis=2)
    mask = np.clip((lum - t) / max(0.001, 1.0 - t), 0, 1)
    blurred = np.asarray(base.filter(ImageFilter.GaussianBlur(float(radius)))).astype(np.float32) / 255.0
    layer = blurred * mask[:, :, None] * float(strength)
    out = 1.0 - (1.0 - arr) * (1.0 - layer)
    return Image.fromarray((np.clip(out, 0, 1) * 255).astype(np.uint8), "RGB")


def soft_skin(img, strength=0.45, radius=3.0):
    """A light local retouch: gentle smoothing plus detail recovery."""
    base = img.convert("RGB")
    blurred = base.filter(ImageFilter.GaussianBlur(float(radius)))
    mixed = Image.blend(base, blurred, max(0.0, min(1.0, float(strength))))
    return mixed.filter(ImageFilter.UnsharpMask(radius=1.2, percent=55, threshold=3))


def apply_local_filter(kind, base64_data, params=None):
    params = params or {}
    img = open_image(base64_data)
    if kind == "freq":
        out = frequency_separation(
            img,
            radius=float(params.get("radius", 8)),
            texture=float(params.get("texture", 0.55)),
        )
    elif kind == "glow":
        out = glow(
            img,
            threshold=int(params.get("threshold", 190)),
            radius=int(params.get("radius", 18)),
            strength=float(params.get("strength", 0.55)),
        )
    elif kind == "soft":
        out = soft_skin(
            img,
            strength=float(params.get("strength", 0.45)),
            radius=float(params.get("radius", 3)),
        )
    else:
        raise ValueError("未知的本地滤镜: %s" % kind)
    return {"base64": encode_png(out), "kind": kind, "width": out.width, "height": out.height}
