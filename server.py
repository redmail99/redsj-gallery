#!/usr/bin/env python3
"""
RedSJ Cover Gallery + Promo Cards — integrated single server on port 8081.
"""
import hashlib
import json
import math
import os
import random
import shutil
import socket
import subprocess
import sys
from io import BytesIO
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from html import escape

import requests
from PIL import Image, ImageDraw, ImageFont

# ─── paths ─────────────────────────────────────────────────────────────────
BASE = Path(__file__).parent
PORT = int(os.getenv("PORT", "8081"))
FONTS_DIR = Path("/home/redmail99/social-story-generator/fonts")
OUTPUT_DIR = BASE / "output"
CACHE_DIR = BASE / ".cache"
for d in (OUTPUT_DIR, CACHE_DIR):
    d.mkdir(parents=True, exist_ok=True)

CARD_W, CARD_H = 1200, 630
COVER_SIZE = 360
PORTRAIT_W, PORTRAIT_H = 540, 960
PORTRAIT_COVER_SIZE = 380
RADIUS = 24

# ─── palette pools ─────────────────────────────────────────────────────────
PALETTES = [
    {"bg1": (10, 5, 30), "bg2": (40, 15, 60), "accent": (210, 180, 140), "glow": (180, 140, 255)},
    {"bg1": (20, 5, 35), "bg2": (60, 20, 80), "accent": (255, 215, 150), "glow": (200, 150, 255)},
    {"bg1": (5, 10, 40), "bg2": (15, 40, 70), "accent": (180, 220, 255), "glow": (100, 180, 255)},
    {"bg1": (10, 25, 20), "bg2": (30, 50, 60), "accent": (150, 255, 200), "glow": (100, 200, 180)},
    {"bg1": (40, 10, 20), "bg2": (70, 25, 50), "accent": (255, 200, 150), "glow": (255, 150, 200)},
    {"bg1": (20, 15, 5), "bg2": (50, 35, 15), "accent": (255, 220, 120), "glow": (255, 200, 80)},
    {"bg1": (5, 5, 20), "bg2": (20, 15, 45), "accent": (200, 200, 255), "glow": (150, 150, 255)},
    {"bg1": (30, 5, 5), "bg2": (60, 20, 10), "accent": (255, 180, 100), "glow": (255, 120, 60)},
    {"bg1": (0, 0, 10), "bg2": (10, 5, 30), "accent": (180, 180, 220), "glow": (100, 100, 200)},
    {"bg1": (25, 10, 35), "bg2": (50, 20, 60), "accent": (220, 180, 255), "glow": (200, 130, 255)},
]

STAR_COLORS = [
    (255, 255, 255, 200), (255, 255, 200, 180), (200, 220, 255, 160),
    (255, 200, 200, 150), (200, 255, 200, 140), (255, 220, 180, 170),
]

ACCENT_SHAPES = ["triangles", "lines", "circles", "waves", "diamonds", "hexagons"]

DATE_OVERRIDES = {
    "Luminal Heartbeats": None,
    "The wizard's words": None,
    "A new dawn": None,
    "Introspection": None,
    "Echoes of infinity": None,
    "We share a world": "May 15, 2025",
    "Beneath the Stars": None,
    "Memories of Tomorrow": None,
    "Inner Dialogue": None,
    "Signal from a distant heart": None,
    "Where We Once Walked": None,
    "Where We Once Walked (Reflection)": None,
}

FULL_DATES = {
    "Where We Once Walked (Reflection)": "Jun 1, 2026",
    "Where We Once Walked": "May 14, 2026",
    "Signal from a distant heart": "Mar 19, 2026",
    "Inner Dialogue": "Feb 5, 2026",
    "Memories of Tomorrow": "Dec 11, 2025",
    "Beneath the Stars": "Nov 27, 2025",
    "We share a world": "May 15, 2025",
    "Introspection": "Nov 28, 2024",
    "A new dawn": "Sep 6, 2024",
    "Echoes of infinity": "Apr 3, 2024",
    "Luminal Heartbeats": "Sep 3, 2023",
    "The wizard's words": "Apr 20, 2023",
}


def _load_font(name: str, size: int) -> ImageFont.FreeTypeFont:
    path = FONTS_DIR / f"{name}.ttf"
    if path.exists():
        return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def _lerp_color(a: tuple, b: tuple, t: float) -> tuple:
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _random_palette() -> dict:
    p = random.choice(PALETTES).copy()
    p["bg1"] = tuple(min(255, max(0, c + random.randint(-10, 10))) for c in p["bg1"])
    p["bg2"] = tuple(min(255, max(0, c + random.randint(-10, 10))) for c in p["bg2"])
    return p


def _render_gradient(w: int, h: int, p: dict) -> Image.Image:
    base = Image.new("RGB", (w, h), p["bg1"])
    c1, c2 = p["bg1"], p["bg2"]
    pixels = []
    for y in range(h):
        t = y / h
        r = int(c1[0] + (c2[0] - c1[0]) * t)
        g = int(c1[1] + (c2[1] - c1[1]) * t)
        b = int(c1[2] + (c2[2] - c1[2]) * t)
        pixels.extend([r, g, b] * w)
    grad_img = Image.new("RGB", (w, h))
    grad_img.frombytes(bytes(pixels))
    return Image.blend(base, grad_img, 0.6)


def _draw_stars(draw: ImageDraw, w: int, h: int, count: int = None):
    count = count or random.randint(60, 150)
    for _ in range(count):
        x = random.randint(0, w)
        y = random.randint(0, h)
        r = random.uniform(0.5, 3.5)
        alpha = random.randint(80, 255)
        color = random.choice(STAR_COLORS)
        c = (*color[:3], alpha)
        draw.ellipse([x - r, y - r, x + r, y + r], fill=c)
        if r > 2:
            for g in range(2):
                gr = r * (2 + g * 1.5)
                ga = int(alpha * (0.15 - g * 0.05))
                if ga > 0:
                    draw.ellipse([x - gr, y - gr, x + gr, y + gr], fill=(*color[:3], ga))


def _draw_geometric(draw: ImageDraw, w: int, h: int, p: dict):
    accent = p["accent"]
    glow = p["glow"]
    n_shapes = random.randint(2, 5)
    selected = random.sample(ACCENT_SHAPES, min(n_shapes, len(ACCENT_SHAPES)))

    for style in selected:
        sym = random.choice(["none", "h", "v", "both", "radial"])

        if style == "triangles":
            _draw_triangles(draw, w, h, accent, glow, sym)
        elif style == "lines":
            _draw_lines(draw, w, h, accent, glow, sym)
        elif style == "circles":
            _draw_circles(draw, w, h, accent, glow, sym)
        elif style == "waves":
            _draw_waves(draw, w, h, accent, sym)
        elif style == "diamonds":
            _draw_diamonds(draw, w, h, accent, glow, sym)
        elif style == "hexagons":
            _draw_hexagons(draw, w, h, accent, glow, sym)


def _mirror_h(pts: list, w: int) -> list:
    return [(w - x, y) for (x, y) in pts]


def _mirror_v(pts: list, h: int) -> list:
    return [(x, h - y) for (x, y) in pts]


def _mirror_radial(pts: list, cx: float, cy: float, n: int) -> list[list]:
    groups = []
    for i in range(1, n):
        angle = 2 * math.pi * i / n
        cos_a, sin_a = math.cos(angle), math.sin(angle)
        groups.append([(cx + (x - cx) * cos_a - (y - cy) * sin_a,
                        cy + (x - cx) * sin_a + (y - cy) * cos_a) for (x, y) in pts])
    return groups


def _draw_triangles(draw, w, h, accent, glow, sym):
    for _ in range(random.randint(4, 10)):
        cx = random.randint(w // 4, 3 * w // 4) if sym != "none" else random.randint(0, w)
        cy = random.randint(h // 4, 3 * h // 4) if sym != "none" else random.randint(0, h)
        s = random.randint(20, 100)
        a = random.uniform(0, math.pi * 2)
        pts = [(cx + math.cos(a + i * math.pi * 2 / 3) * s,
                cy + math.sin(a + i * math.pi * 2 / 3) * s) for i in range(3)]
        col = _lerp_color(accent, glow, random.random()) + (random.randint(12, 45),)
        draw.polygon(pts, fill=col, outline=(*accent[:3], 20))
        if sym == "h":
            pts2 = _mirror_h(pts, w)
            draw.polygon(pts2, fill=col, outline=(*accent[:3], 20))
        elif sym == "v":
            pts2 = _mirror_v(pts, h)
            draw.polygon(pts2, fill=col, outline=(*accent[:3], 20))
        elif sym == "both":
            pts2 = _mirror_h(pts, w)
            pts3 = _mirror_v(pts, h)
            pts4 = _mirror_h(_mirror_v(pts, h), w)
            for p in (pts2, pts3, pts4):
                draw.polygon(p, fill=col, outline=(*accent[:3], 20))
        elif sym == "radial":
            for group in _mirror_radial(pts, w / 2, h / 2, random.randint(3, 6)):
                draw.polygon(group, fill=col, outline=(*accent[:3], 15))


def _draw_lines(draw, w, h, accent, glow, sym):
    for _ in range(random.randint(3, 10)):
        x1, y1 = random.randint(w // 4, 3 * w // 4), random.randint(h // 4, 3 * h // 4)
        x2 = random.randint(w // 4, 3 * w // 4) if sym != "none" else random.randint(0, w)
        y2 = random.randint(h // 4, 3 * h // 4) if sym != "none" else random.randint(0, h)
        col = (*_lerp_color(accent, glow, random.random()), random.randint(15, 50))
        wd = random.randint(1, 3)
        draw.line([(x1, y1), (x2, y2)], fill=col, width=wd)
        if sym == "h":
            draw.line([(w - x1, y1), (w - x2, y2)], fill=col, width=wd)
        elif sym == "v":
            draw.line([(x1, h - y1), (x2, h - y2)], fill=col, width=wd)
        elif sym == "both":
            draw.line([(w - x1, y1), (w - x2, y2)], fill=col, width=wd)
            draw.line([(x1, h - y1), (x2, h - y2)], fill=col, width=wd)
            draw.line([(w - x1, h - y1), (w - x2, h - y2)], fill=col, width=wd)
        elif sym == "radial":
            for group in _mirror_radial([(x1, y1), (x2, y2)], w / 2, h / 2, random.randint(4, 8)):
                draw.line(group, fill=col, width=wd)
    # center-burst radial lines (symmetric by nature)
    if random.random() < 0.5:
        cx, cy = w / 2, h / 2
        for _ in range(random.randint(6, 14)):
            a = random.uniform(0, math.pi * 2)
            l = random.randint(80, 280)
            col = (*glow[:3], random.randint(12, 30))
            draw.line([(cx, cy), (cx + math.cos(a) * l, cy + math.sin(a) * l)], fill=col, width=1)


def _draw_circles(draw, w, h, accent, glow, sym):
    for _ in range(random.randint(3, 8)):
        cx = random.randint(w // 4, 3 * w // 4) if sym != "none" else random.randint(0, w)
        cy = random.randint(h // 4, 3 * h // 4) if sym != "none" else random.randint(0, h)
        r = random.randint(20, 140)
        col = (*accent[:3], random.randint(10, 30))
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=col, width=random.randint(1, 2))
        if sym == "h":
            draw.ellipse([w - cx - r, cy - r, w - cx + r, cy + r], outline=col, width=1)
        elif sym == "v":
            draw.ellipse([cx - r, h - cy - r, cx + r, h - cy + r], outline=col, width=1)
        elif sym == "both":
            draw.ellipse([w - cx - r, cy - r, w - cx + r, cy + r], outline=col, width=1)
            draw.ellipse([cx - r, h - cy - r, cx + r, h - cy + r], outline=col, width=1)
            draw.ellipse([w - cx - r, h - cy - r, w - cx + r, h - cy + r], outline=col, width=1)
        elif sym == "radial":
            for i in range(1, random.randint(3, 5)):
                a = 2 * math.pi * i / random.randint(3, 5)
                nx = w / 2 + (cx - w / 2) * math.cos(a) - (cy - h / 2) * math.sin(a)
                ny = h / 2 + (cx - w / 2) * math.sin(a) + (cy - h / 2) * math.cos(a)
                draw.ellipse([nx - r, ny - r, nx + r, ny + r], outline=col, width=1)


def _draw_waves(draw, w, h, accent, sym):
    for _ in range(random.randint(2, 5)):
        sy = random.randint(h // 4, 3 * h // 4) if sym != "none" else random.randint(0, h)
        amp = random.randint(15, 50)
        freq = random.uniform(0.003, 0.018)
        pts = [(x, sy + math.sin(x * freq) * amp + random.randint(-4, 4)) for x in range(0, w, 4)]
        col = (*accent[:3], random.randint(20, 40))
        draw.line(pts, fill=col, width=random.randint(1, 2))
        if sym == "v":
            pts2 = [(x, h - (sy + math.sin(x * freq) * amp)) for x in range(0, w, 4)]
            draw.line(pts2, fill=col, width=1)
        elif sym == "both":
            pts2 = [(x, h - (sy + math.sin(x * freq) * amp)) for x in range(0, w, 4)]
            draw.line(pts2, fill=col, width=1)
            # mirrored horizontal copy at reduced opacity
            col2 = (*accent[:3], random.randint(8, 18))
            pts3 = [(w - x, sy + math.sin(x * freq) * amp + random.randint(-4, 4)) for x in range(0, w, 4)]
            draw.line(pts3, fill=col2, width=1)


def _draw_diamonds(draw, w, h, accent, glow, sym):
    for _ in range(random.randint(3, 6)):
        cx = random.randint(w // 4, 3 * w // 4) if sym != "none" else random.randint(0, w)
        cy = random.randint(h // 4, 3 * h // 4) if sym != "none" else random.randint(0, h)
        s = random.randint(20, 80)
        pts = [(cx, cy - s), (cx + s, cy), (cx, cy + s), (cx - s, cy)]
        col = (*_lerp_color(accent, glow, random.random()), random.randint(15, 35))
        draw.polygon(pts, fill=col, outline=(*accent[:3], 25))
        if sym == "h":
            pts2 = _mirror_h(pts, w)
            draw.polygon(pts2, fill=col, outline=(*accent[:3], 25))
        elif sym == "v":
            pts2 = _mirror_v(pts, h)
            draw.polygon(pts2, fill=col, outline=(*accent[:3], 25))
        elif sym == "both":
            for p in (_mirror_h(pts, w), _mirror_v(pts, h), _mirror_h(_mirror_v(pts, h), w)):
                draw.polygon(p, fill=col, outline=(*accent[:3], 25))
        elif sym == "radial":
            for group in _mirror_radial(pts, w / 2, h / 2, random.randint(3, 5)):
                draw.polygon(group, fill=col, outline=(*accent[:3], 15))


def _draw_hexagons(draw, w, h, accent, glow, sym):
    for _ in range(random.randint(2, 5)):
        cx = random.randint(w // 4, 3 * w // 4) if sym != "none" else random.randint(0, w)
        cy = random.randint(h // 4, 3 * h // 4) if sym != "none" else random.randint(0, h)
        s = random.randint(20, 70)
        pts = [(cx + math.cos(i * math.pi / 3 - math.pi / 6) * s,
                cy + math.sin(i * math.pi / 3 - math.pi / 6) * s) for i in range(6)]
        col = (*_lerp_color(accent, glow, random.random()), random.randint(12, 30))
        draw.polygon(pts, fill=col, outline=(*accent[:3], 20))
        if sym == "h":
            draw.polygon(_mirror_h(pts, w), fill=col, outline=(*accent[:3], 20))
        elif sym == "v":
            draw.polygon(_mirror_v(pts, h), fill=col, outline=(*accent[:3], 20))
        elif sym == "both":
            for p in (_mirror_h(pts, w), _mirror_v(pts, h), _mirror_h(_mirror_v(pts, h), w)):
                draw.polygon(p, fill=col, outline=(*accent[:3], 20))
        elif sym == "radial":
            for group in _mirror_radial(pts, w / 2, h / 2, random.randint(3, 6)):
                draw.polygon(group, fill=col, outline=(*accent[:3], 15))


def _draw_cosmic_dust(draw: ImageDraw, w: int, h: int, p: dict):
    for _ in range(random.randint(15, 40)):
        cx, cy = random.randint(0, w), random.randint(0, h)
        for _ in range(random.randint(3, 12)):
            x = cx + random.randint(-30, 30)
            y = cy + random.randint(-30, 30)
            r = random.uniform(0.3, 1.5)
            c = _lerp_color(p["accent"], p["glow"], random.random()) + (random.randint(30, 100),)
            draw.ellipse([x - r, y - r, x + r, y + r], fill=c)


def _round_corners(img: Image.Image, r: int) -> Image.Image:
    mask = Image.new("L", img.size, 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle([(0, 0), (img.width - 1, img.height - 1)], r, fill=255)
    result = img.copy()
    result.putalpha(mask)
    return result


def _draw_glow(draw: ImageDraw, cx: int, cy: int, r: int, color: tuple, alpha: int = 30):
    for i in range(3):
        gr = r * (1 + i * 0.6)
        ga = alpha // (i + 2)
        if ga > 0:
            draw.ellipse([cx - gr, cy - gr, cx + gr, cy + gr], fill=(*color[:3], ga))


def generate_card(cover_url: str, title: str, date: str = "", style_seed: int = None) -> Path:
    if style_seed is not None:
        random.seed(style_seed)
    p = _random_palette()

    card = Image.new("RGBA", (CARD_W, CARD_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(card)
    bg = _render_gradient(CARD_W, CARD_H, p)
    card = Image.alpha_composite(card, bg.convert("RGBA"))
    draw = ImageDraw.Draw(card)

    _draw_cosmic_dust(draw, CARD_W, CARD_H, p)
    _draw_geometric(draw, CARD_W, CARD_H, p)
    _draw_stars(draw, CARD_W, CARD_H)
    cover_key = hashlib.md5(cover_url.encode()).hexdigest()
    cache_path = CACHE_DIR / f"{cover_key}.jpg"
    if cache_path.exists():
        cover_img = Image.open(cache_path).convert("RGBA")
    else:
        try:
            r = requests.get(cover_url, timeout=15)
            r.raise_for_status()
            cover_img = Image.open(BytesIO(r.content)).convert("RGBA")
            cache_path.write_bytes(r.content)
        except Exception as e:
            print(f"  cover download: {e}", file=sys.stderr)
            cover_img = Image.new("RGBA", (COVER_SIZE, COVER_SIZE), p["accent"] + (255,))

    cover_img = cover_img.resize((COVER_SIZE, COVER_SIZE), Image.LANCZOS)
    cover_img = _round_corners(cover_img, RADIUS)
    cx, cy = 80 + COVER_SIZE // 2, CARD_H // 2
    _draw_glow(draw, cx, cy, COVER_SIZE // 2 + 20, p["glow"], 40)
    card.paste(cover_img, (80, (CARD_H - COVER_SIZE) // 2), cover_img)

    tf = _load_font("GreatVibes-Regular", 72)
    df = _load_font("Cookie-Regular", 32)
    tx = 80 + COVER_SIZE + 50

    # vertically center text block to the right of cover
    cx = CARD_H // 2
    # Title — bigger, vertically centered, split into 2 lines if >15 chars
    tc = tuple(min(255, c + 40) for c in p["accent"][:3])
    if len(title) > 15:
        mid = len(title) // 2
        # find nearest space to split
        left_space = title.rfind(" ", 0, mid)
        right_space = title.find(" ", mid)
        if left_space > 0 and (right_space == -1 or mid - left_space <= right_space - mid):
            split = left_space
        elif right_space > 0:
            split = right_space
        else:
            split = mid
        line1 = title[:split].strip()
        line2 = title[split:].strip()
        bbox1 = draw.textbbox((0, 0), line1, font=tf)
        bbox2 = draw.textbbox((0, 0), line2, font=tf)
        th1 = bbox1[3] - bbox1[1]
        th2 = bbox2[3] - bbox2[1]
        gap = 8
        total_h = th1 + gap + th2
        draw.text((tx, cx - total_h // 2), line1, font=tf, fill=tc)
        draw.text((tx, cx - total_h // 2 + th1 + gap), line2, font=tf, fill=tc)
        th = total_h  # for date positioning below
    else:
        bbox = draw.textbbox((0, 0), title, font=tf)
        th = bbox[3] - bbox[1]
        draw.text((tx, cx - th // 2), title, font=tf, fill=tc)
    # Date below title — same color as title
    if date:
        draw.text((tx, cx + th // 2 + 20), date, font=df, fill=tc)

    draw.rounded_rectangle([(2, 2), (CARD_W - 3, CARD_H - 3)],
                           radius=12, outline=p["glow"] + (20,), width=1)

    out_path = OUTPUT_DIR / f"card_{cover_key[:12]}.png"
    card.convert("RGB").save(out_path, quality=92)
    return out_path


def generate_portrait_card(cover_url: str, title: str, date: str = "", style_seed: int = None) -> Path:
    """9:16 portrait card — cover at top, text at bottom, same cosmic effects."""
    if style_seed is not None:
        random.seed(style_seed)
    p = _random_palette()

    card = Image.new("RGBA", (PORTRAIT_W, PORTRAIT_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(card)
    bg = _render_gradient(PORTRAIT_W, PORTRAIT_H, p)
    card = Image.alpha_composite(card, bg.convert("RGBA"))
    draw = ImageDraw.Draw(card)

    _draw_cosmic_dust(draw, PORTRAIT_W, PORTRAIT_H, p)
    _draw_geometric(draw, PORTRAIT_W, PORTRAIT_H, p)
    _draw_stars(draw, PORTRAIT_W, PORTRAIT_H)
    cover_key = hashlib.md5(cover_url.encode()).hexdigest()
    cache_path = CACHE_DIR / f"{cover_key}.jpg"
    if cache_path.exists():
        cover_img = Image.open(cache_path).convert("RGBA")
    else:
        try:
            r = requests.get(cover_url, timeout=15)
            r.raise_for_status()
            cover_img = Image.open(BytesIO(r.content)).convert("RGBA")
            cache_path.write_bytes(r.content)
        except Exception as e:
            print(f"  cover download: {e}", file=sys.stderr)
            cover_img = Image.new("RGBA", (PORTRAIT_COVER_SIZE, PORTRAIT_COVER_SIZE), p["accent"] + (255,))

    cover_img = cover_img.resize((PORTRAIT_COVER_SIZE, PORTRAIT_COVER_SIZE), Image.LANCZOS)
    cover_img = _round_corners(cover_img, RADIUS)
    # Cover at top, centered
    cover_x = (PORTRAIT_W - PORTRAIT_COVER_SIZE) // 2
    cover_y = 50
    _draw_glow(draw, PORTRAIT_W // 2, cover_y + PORTRAIT_COVER_SIZE // 2,
               PORTRAIT_COVER_SIZE // 2 + 20, p["glow"], 40)
    card.paste(cover_img, (cover_x, cover_y), cover_img)

    # Text at bottom — centered
    tf = _load_font("GreatVibes-Regular", 64)
    df = _load_font("Cookie-Regular", 30)
    tc = tuple(min(255, c + 40) for c in p["accent"][:3])
    text_area_top = cover_y + PORTRAIT_COVER_SIZE + 50
    text_area_bottom = PORTRAIT_H - 40
    text_area_h = text_area_bottom - text_area_top

    if len(title) > 15:
        mid = len(title) // 2
        left_space = title.rfind(" ", 0, mid)
        right_space = title.find(" ", mid)
        if left_space > 0 and (right_space == -1 or mid - left_space <= right_space - mid):
            split = left_space
        elif right_space > 0:
            split = right_space
        else:
            split = mid
        line1 = title[:split].strip()
        line2 = title[split:].strip()
        bbox1 = draw.textbbox((0, 0), line1, font=tf)
        bbox2 = draw.textbbox((0, 0), line2, font=tf)
        th1 = bbox1[3] - bbox1[1]
        th2 = bbox2[3] - bbox2[1]
        gap = 8
        total_h = th1 + gap + th2
        ty = text_area_top + (text_area_h - total_h) // 2
        draw.text(((PORTRAIT_W - bbox1[2]) // 2, ty), line1, font=tf, fill=tc)
        draw.text(((PORTRAIT_W - bbox2[2]) // 2, ty + th1 + gap), line2, font=tf, fill=tc)
        th = total_h
    else:
        bbox = draw.textbbox((0, 0), title, font=tf)
        th = bbox[3] - bbox[1]
        ty = text_area_top + (text_area_h - th) // 2
        draw.text(((PORTRAIT_W - bbox[2]) // 2, ty), title, font=tf, fill=tc)

    if date:
        db = draw.textbbox((0, 0), date, font=df)
        draw.text(((PORTRAIT_W - db[2]) // 2, ty + th + 16), date, font=df, fill=tc)

    draw.rounded_rectangle([(2, 2), (PORTRAIT_W - 3, PORTRAIT_H - 3)],
                           radius=12, outline=p["glow"] + (20,), width=1)

    out_path = OUTPUT_DIR / f"portrait_{cover_key[:12]}.png"
    card.convert("RGB").save(out_path, quality=92)
    return out_path


def generate_all_cards(force: bool = False) -> list[dict]:
    from app import fetch_covers
    covers = fetch_covers()

    cards = []
    batch_seed = random.randint(0, 999999) if force else 0
    for i, c in enumerate(covers):
        name = c["name"]
        date = FULL_DATES.get(name, c.get("year", ""))
        seed = batch_seed + i if force else i
        out = generate_card(c["url"], name, date, style_seed=seed + 1)
        cards.append({"name": name, "date": date,
                      "type": c.get("type", ""), "file": out.name})
        print(f"  {name}")
    return cards


def generate_all_portrait_cards(force: bool = False) -> list[dict]:
    from app import fetch_covers
    covers = fetch_covers()

    cards = []
    batch_seed = random.randint(0, 999999) if force else 0
    for i, c in enumerate(covers):
        name = c["name"]
        date = FULL_DATES.get(name, c.get("year", ""))
        seed = batch_seed + i if force else i
        out = generate_portrait_card(c["url"], name, date, style_seed=seed + 1)
        cards.append({"name": name, "date": date,
                      "type": c.get("type", ""), "file": out.name})
        print(f"  portrait {name}")
    return cards


def write_cards_json(cards: list[dict]):
    (BASE / "cards.json").write_text(json.dumps({"count": len(cards), "cards": cards}))


def write_portrait_cards_json(cards: list[dict]):
    (BASE / "portrait-cards.json").write_text(json.dumps({"count": len(cards), "cards": cards}))


# ─── HTTP handler ──────────────────────────────────────────────────────────
class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(BASE), **kwargs)

    def do_GET(self):
        if self.path == "/refresh":
            self.send_response(302)
            self.send_header("Location", "/")
            self.end_headers()
            subprocess.run([sys.executable, str(BASE / "app.py")], cwd=str(BASE))
            return
        if self.path == "/cards.json":
            return self._serve_cards_json()
        if self.path == "/portrait-cards.json":
            return self._serve_portrait_cards_json()
        if self.path.startswith("/output/"):
            return self._serve_output()
        if self.path.startswith("/portrait-output/"):
            return self._serve_portrait_output()
        super().do_GET()

    def do_POST(self):
        if self.path == "/regenerate":
            return self._handle_regenerate()
        if self.path == "/regenerate-portraits":
            return self._handle_regenerate_portraits()
        self.send_error(404)

    def _serve_cards_json(self):
        p = BASE / "cards.json"
        if not p.exists():
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"count":0,"cards":[]}')
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(p.read_bytes())

    def _serve_output(self):
        path_only = self.path.split("?", 1)[0]
        rel = path_only[len("/output/"):]
        f = OUTPUT_DIR / rel
        if f.exists() and f.is_file():
            ctype = "image/png" if f.suffix == ".png" else "image/jpeg"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(f.read_bytes())
        else:
            self.send_error(404)

    def _handle_regenerate(self):
        try:
            shutil.rmtree(OUTPUT_DIR, ignore_errors=True)
            shutil.rmtree(CACHE_DIR, ignore_errors=True)
            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cards = generate_all_cards(force=True)
            write_cards_json(cards)
            data = {"success": True, "count": len(cards), "cards": cards}
        except Exception as e:
            data = {"success": False, "error": str(e)}
            print(f"[regenerate] {e}", file=sys.stderr)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def _serve_portrait_cards_json(self):
        p = BASE / "portrait-cards.json"
        if not p.exists():
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"count":0,"cards":[]}')
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(p.read_bytes())

    def _serve_portrait_output(self):
        path_only = self.path.split("?", 1)[0]
        rel = path_only[len("/portrait-output/"):]
        f = OUTPUT_DIR / rel
        if f.exists() and f.is_file():
            ctype = "image/png" if f.suffix == ".png" else "image/jpeg"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(f.read_bytes())
        else:
            self.send_error(404)

    def _handle_regenerate_portraits(self):
        try:
            cards = generate_all_portrait_cards(force=True)
            write_portrait_cards_json(cards)
            data = {"success": True, "count": len(cards), "cards": cards}
        except Exception as e:
            data = {"success": False, "error": str(e)}
            print(f"[regenerate-portraits] {e}", file=sys.stderr)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def log_message(self, fmt, *args):
        if args and str(args[0]) in ("/cards.json", "/regenerate",
                                      "/portrait-cards.json", "/regenerate-portraits"):
            return
        super().log_message(fmt, *args)


if __name__ == "__main__":
    if not (BASE / "cards.json").exists():
        print("Generating initial promo cards...")
        cards = generate_all_cards()
        write_cards_json(cards)
        print(f"  {len(cards)} cards written")

    if not (BASE / "portrait-cards.json").exists():
        print("Generating portrait promo cards...")
        pcards = generate_all_portrait_cards()
        write_portrait_cards_json(pcards)
        print(f"  {len(pcards)} portrait cards written")

    subprocess.run([sys.executable, str(BASE / "app.py")], cwd=str(BASE))

    server = HTTPServer(("0.0.0.0", PORT), Handler, bind_and_activate=False)
    server.allow_reuse_address = True
    server.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.server_bind()
    server.server_activate()
    print(f"RedSJ Gallery + Cards — http://localhost:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
