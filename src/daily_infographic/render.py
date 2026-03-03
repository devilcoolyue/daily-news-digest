from __future__ import annotations

import hashlib
import ipaddress
import os
import re
from datetime import date
from pathlib import Path
from typing import Iterable, Sequence
import urllib.error
import urllib.parse
import urllib.request

from PIL import Image, ImageDraw, ImageFont

from .models import Card, DomainConfig


BASE_FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Songti.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
]

TITLE_ZH_FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Kaiti.ttc",
    "/System/Library/Fonts/Supplemental/STKaiti.ttf",
    "/System/Library/Fonts/Supplemental/Songti.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
]

BODY_ZH_FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Songti.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
]

EN_FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Times New Roman.ttf",
    "/System/Library/Fonts/Supplemental/Georgia.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
]

ALLOWED_ICON_KINDS = {
    "openai",
    "meta",
    "google",
    "anthropic",
    "minimax",
    "apple",
    "trend",
    "chip",
    "robot",
    "megaphone",
    "brain",
    "spark",
}

SUPPORTED_ICON_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}
MAX_LAYOUT_CARDS = 12


# Grid rectangles: (x, y, w, h) in a 12x12 virtual grid.
# Rule: slot 0 is always the primary headline and must be visually larger.
LAYOUT_PATTERNS = [
    # Hero-heavy style.
    [
        (0, 0, 8, 4),
        (8, 0, 4, 2),
        (8, 2, 4, 2),
        (0, 4, 4, 3),
        (4, 4, 4, 3),
        (8, 4, 4, 3),
        (0, 7, 6, 2),
        (6, 7, 6, 2),
        (0, 9, 3, 3),
        (3, 9, 3, 3),
        (6, 9, 3, 3),
        (9, 9, 3, 3),
    ],
    # Wide hero with compact right column.
    [
        (0, 0, 7, 4),
        (7, 0, 5, 2),
        (7, 2, 5, 2),
        (0, 4, 4, 3),
        (4, 4, 4, 3),
        (8, 4, 4, 3),
        (0, 7, 6, 2),
        (6, 7, 6, 2),
        (0, 9, 3, 3),
        (3, 9, 3, 3),
        (6, 9, 3, 3),
        (9, 9, 3, 3),
    ],
    # Balanced hero with a tall secondary block.
    [
        (0, 0, 6, 4),
        (6, 0, 3, 4),
        (9, 0, 3, 2),
        (9, 2, 3, 2),
        (0, 4, 4, 3),
        (4, 4, 4, 3),
        (8, 4, 4, 3),
        (0, 7, 4, 2),
        (4, 7, 4, 2),
        (8, 7, 4, 2),
        (0, 9, 6, 3),
        (6, 9, 6, 3),
    ],
]


def _load_font(
    size: int,
    candidates: list[str] | tuple[str, ...] | None = None,
) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    font_paths = list(candidates or []) + BASE_FONT_CANDIDATES
    for path in font_paths:
        try:
            return ImageFont.truetype(path, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _pick_text_font(
    text: str,
    size: int,
    role: str,
) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    cjk_count = len(re.findall(r"[\u4e00-\u9fff]", text or ""))
    latin_count = len(re.findall(r"[A-Za-z]", text or ""))
    # Avoid tofu (square glyphs) on mixed Chinese/English lines:
    # if any CJK exists, keep CJK-capable fonts; English font is only for pure English text.
    prefer_english = latin_count > 0 and cjk_count == 0

    if role == "title":
        zh_candidates = TITLE_ZH_FONT_CANDIDATES
    elif role in {"body", "footer", "chip"}:
        zh_candidates = BODY_ZH_FONT_CANDIDATES
    else:
        zh_candidates = BASE_FONT_CANDIDATES

    if prefer_english:
        return _load_font(size, candidates=EN_FONT_CANDIDATES + zh_candidates)
    # Never put English-only fonts in front for CJK text; otherwise Chinese can render as tofu squares.
    return _load_font(size, candidates=zh_candidates)


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    text = hex_color.lstrip("#")
    if len(text) != 6:
        return (240, 245, 255)
    return tuple(int(text[i : i + 2], 16) for i in (0, 2, 4))


def _mix(c1: tuple[int, int, int], c2: tuple[int, int, int], ratio: float) -> tuple[int, int, int]:
    ratio = max(0.0, min(1.0, ratio))
    return tuple(int(c1[i] + (c2[i] - c1[i]) * ratio) for i in range(3))


def _text_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> int:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0]


def _truncate_text_to_width(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    max_width: int,
) -> str:
    clean = " ".join((text or "").split())
    if not clean:
        return ""
    if _text_width(draw, clean, font) <= max_width:
        return clean

    suffix = "…"
    body = clean
    while body:
        body = body[:-1].rstrip()
        candidate = body + suffix
        if _text_width(draw, candidate, font) <= max_width:
            return candidate
    return suffix


def _fit_text_and_font(
    draw: ImageDraw.ImageDraw,
    text: str,
    max_width: int,
    base_size: int,
    min_size: int,
    role: str = "body",
) -> tuple[str, ImageFont.ImageFont]:
    safe_max = max(20, int(max_width))
    start = max(8, int(base_size))
    floor = max(8, int(min_size))

    for size in range(start, floor - 1, -1):
        font = _pick_text_font(text, size, role=role)
        if _text_width(draw, text, font) <= safe_max:
            return text, font

    font = _pick_text_font(text, floor, role=role)
    return _truncate_text_to_width(draw, text, font, safe_max), font


def _is_safe_icon_url(url: str) -> bool:
    raw = (url or "").strip()
    if not raw:
        return False
    try:
        parsed = urllib.parse.urlparse(raw)
    except ValueError:
        return False
    if parsed.scheme.lower() != "https":
        return False

    host = (parsed.hostname or "").lower()
    if not host:
        return False
    if host in {"localhost", "127.0.0.1", "::1"} or host.endswith(".local"):
        return False
    try:
        addr = ipaddress.ip_address(host)
        if (
            addr.is_private
            or addr.is_loopback
            or addr.is_link_local
            or addr.is_multicast
            or addr.is_reserved
        ):
            return False
    except ValueError:
        pass
    return True


def _icon_cache_path(icon_url: str, cache_dir: Path) -> Path:
    parsed = urllib.parse.urlparse(icon_url)
    ext = Path(parsed.path).suffix.lower()
    if ext not in SUPPORTED_ICON_EXTS:
        ext = ".img"
    digest = hashlib.sha1(icon_url.encode("utf-8")).hexdigest()
    return cache_dir / f"{digest}{ext}"


def _download_icon_to_cache(icon_url: str, cache_dir: Path, timeout_sec: int) -> Path | None:
    return _download_icon_to_cache_with_proxy(icon_url, cache_dir, timeout_sec, proxy_url="")


def _download_icon_to_cache_with_proxy(
    icon_url: str,
    cache_dir: Path,
    timeout_sec: int,
    proxy_url: str,
) -> Path | None:
    if not _is_safe_icon_url(icon_url):
        return None

    cache_path = _icon_cache_path(icon_url, cache_dir)
    if cache_path.exists() and cache_path.stat().st_size > 0:
        return cache_path

    req = urllib.request.Request(
        url=icon_url,
        headers={
            "User-Agent": "daily-infographic/1.0",
            "Accept": "image/*",
        },
        method="GET",
    )
    try:
        if proxy_url:
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler(
                    {
                        "http": proxy_url,
                        "https": proxy_url,
                    }
                )
            )
            resp_ctx = opener.open(req, timeout=max(2, timeout_sec))
        else:
            resp_ctx = urllib.request.urlopen(req, timeout=max(2, timeout_sec))

        with resp_ctx as resp:
            content_type = str(resp.headers.get("Content-Type", "")).lower()
            if content_type and not content_type.startswith("image/"):
                return None
            data = resp.read()
    except (TimeoutError, urllib.error.URLError, urllib.error.HTTPError, ValueError):
        return None

    if not data or len(data) > 4 * 1024 * 1024:
        return None

    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(data)
    return cache_path


def _load_remote_icon(
    icon_url: str,
    cache_dir: Path,
    size: int,
    timeout_sec: int,
    proxy_url: str,
) -> Image.Image | None:
    cache_path = _download_icon_to_cache_with_proxy(icon_url, cache_dir, timeout_sec, proxy_url)
    if cache_path is None:
        return None

    try:
        with Image.open(cache_path) as raw:
            icon = raw.convert("RGBA")
    except OSError:
        cache_path.unlink(missing_ok=True)
        return None

    target = max(16, int(size * 0.72))
    icon.thumbnail((target, target), Image.Resampling.LANCZOS)

    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ox = (size - icon.width) // 2
    oy = (size - icon.height) // 2
    layer.alpha_composite(icon, (ox, oy))
    return layer


def _wrap_lines_with_state(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    max_width: int,
    max_lines: int,
    add_ellipsis: bool = True,
) -> tuple[list[str], bool]:
    clean = " ".join(text.split())
    if not clean:
        return [""], False

    lines: list[str] = []
    current = ""
    cursor = 0
    truncated = False
    while cursor < len(clean):
        ch = clean[cursor]
        candidate = current + ch
        bbox = draw.textbbox((0, 0), candidate, font=font)
        width = bbox[2] - bbox[0]
        if width <= max_width or not current:
            current = candidate
            cursor += 1
        else:
            lines.append(current)
            current = ""
            if len(lines) >= max_lines:
                truncated = True
                break

    if not truncated and current:
        lines.append(current)

    if len(lines) > max_lines:
        lines = lines[:max_lines]
        truncated = True

    if cursor < len(clean):
        truncated = True

    if add_ellipsis and truncated and lines:
        last = lines[-1]
        while last:
            bbox = draw.textbbox((0, 0), last + "…", font=font)
            if bbox[2] - bbox[0] <= max_width:
                break
            last = last[:-1]
        lines[-1] = (last + "…") if last and clean != "" else last

    return (lines or [""]), truncated


def _wrap_lines(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    max_width: int,
    max_lines: int,
    add_ellipsis: bool = True,
) -> list[str]:
    lines, _ = _wrap_lines_with_state(
        draw,
        text,
        font,
        max_width,
        max_lines,
        add_ellipsis=add_ellipsis,
    )
    return lines


def _fit_lines_and_font(
    draw: ImageDraw.ImageDraw,
    text: str,
    max_width: int,
    max_lines: int,
    base_size: int,
    min_size: int,
    role: str,
) -> tuple[list[str], ImageFont.ImageFont]:
    for size in range(max(8, base_size), max(8, min_size) - 1, -1):
        font = _pick_text_font(text, size, role=role)
        lines, truncated = _wrap_lines_with_state(
            draw,
            text,
            font,
            max_width,
            max_lines,
            add_ellipsis=False,
        )
        if not truncated:
            return lines, font

    fallback_font = _pick_text_font(text, max(8, min_size), role=role)
    fallback_lines, _ = _wrap_lines_with_state(
        draw,
        text,
        fallback_font,
        max_width,
        max_lines,
        add_ellipsis=True,
    )
    return fallback_lines, fallback_font


def _estimate_text_weight(
    title: str,
    summary: str,
    tags: Iterable[str] | None = None,
) -> float:
    blob = " ".join(
        part
        for part in [
            (title or "").strip(),
            (summary or "").strip(),
            " ".join([str(t).strip() for t in (tags or []) if str(t).strip()]),
        ]
        if part
    )
    if not blob:
        return 0.0

    cjk_chars = len(re.findall(r"[\u4e00-\u9fff]", blob))
    latin_tokens = len(re.findall(r"[A-Za-z0-9]+", blob))
    punctuation = len(re.findall(r"[，。！？,.!?;；:：]", blob))
    return float(cjk_chars) + float(latin_tokens) * 2.2 + float(punctuation) * 0.35


def _normalize_values(values: Sequence[float], default: float = 0.5) -> list[float]:
    if not values:
        return []
    lo = min(values)
    hi = max(values)
    if hi - lo < 1e-9:
        return [default for _ in values]
    return [(v - lo) / (hi - lo) for v in values]


def _collect_layout_signals(
    count: int,
    cards: Sequence[Card] | None = None,
    layout_hints: Sequence[dict[str, float]] | None = None,
) -> tuple[list[float], list[float]]:
    scores: list[float] = []
    text_weights: list[float] = []

    for idx in range(max(0, count)):
        hint = layout_hints[idx] if layout_hints and idx < len(layout_hints) else None
        if hint is not None:
            score = float(hint.get("score", 0.0))
            text_weight = float(hint.get("text_weight", 0.0))
        elif cards is not None and idx < len(cards):
            card = cards[idx]
            score = float(card.score)
            text_weight = _estimate_text_weight(card.title, card.summary, card.tags)
        else:
            score = 0.0
            text_weight = 0.0
        scores.append(score)
        text_weights.append(text_weight)

    # Keep deterministic behavior even when signals are missing.
    if len({round(v, 6) for v in scores}) <= 1:
        scores = [float(count - i) for i in range(count)]
    if all(v <= 0.0 for v in text_weights):
        text_weights = [float(count - i) for i in range(count)]

    return scores, text_weights


def _draw_gradient_background(img: Image.Image, start: str, end: str) -> None:
    draw = ImageDraw.Draw(img)
    s = _hex_to_rgb(start)
    e = _hex_to_rgb(end)
    w, h = img.size
    for y in range(h):
        ratio = y / max(1, h - 1)
        color = _mix(s, e, ratio)
        draw.line((0, y, w, y), fill=(*color, 255))


def _draw_background_decorations(img: Image.Image) -> None:
    w, h = img.size
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)

    # Halo + subtle circuits around header.
    d.ellipse((120, -170, 960, 520), outline=(255, 255, 255, 120), width=4)
    d.ellipse((170, -120, 910, 470), outline=(210, 230, 255, 110), width=2)
    d.line((0, 120, 220, 120), fill=(190, 210, 238, 140), width=3)
    d.line((860, 120, w, 120), fill=(190, 210, 238, 140), width=3)

    # Circuit lines on both sides.
    for offset in [180, 260, 340]:
        d.line((0, offset, 70, offset), fill=(180, 205, 236, 120), width=2)
        d.line((70, offset, 120, offset + 30), fill=(180, 205, 236, 120), width=2)
        d.ellipse((115, offset + 25, 125, offset + 35), fill=(190, 220, 255, 150))

        d.line((w - 120, offset, w - 50, offset), fill=(180, 205, 236, 120), width=2)
        d.line((w - 170, offset + 30, w - 120, offset), fill=(180, 205, 236, 120), width=2)
        d.ellipse((w - 175, offset + 25, w - 165, offset + 35), fill=(190, 220, 255, 150))

    # Trend motif.
    bx = w - 220
    by = 170
    bar_w = 18
    for i, bh in enumerate([24, 38, 58]):
        x0 = bx + i * 28
        d.rounded_rectangle((x0, by + 70 - bh, x0 + bar_w, by + 70), radius=4, fill=(173, 205, 240, 110))
    d.line((bx - 6, by + 58, bx + 80, by + 14), fill=(166, 200, 240, 140), width=4)
    d.polygon([(bx + 83, by + 14), (bx + 70, by + 12), (bx + 77, by + 25)], fill=(166, 200, 240, 140))

    # Brain-like dot cluster.
    cx, cy = 140, 186
    points = [(-36, 0), (-15, -20), (10, -16), (30, -3), (22, 20), (-5, 24), (-26, 14)]
    for px, py in points:
        d.ellipse((cx + px - 6, cy + py - 6, cx + px + 6, cy + py + 6), fill=(180, 214, 250, 125))
    connections = [(0, 1), (1, 2), (2, 3), (3, 4), (4, 5), (5, 6), (6, 0), (1, 5)]
    for a, b in connections:
        x1, y1 = points[a]
        x2, y2 = points[b]
        d.line((cx + x1, cy + y1, cx + x2, cy + y2), fill=(170, 205, 244, 120), width=2)

    # Tiny spark dots.
    for x, y in [(260, 90), (780, 92), (812, 145), (318, 180), (700, 190)]:
        d.ellipse((x - 4, y - 4, x + 4, y + 4), fill=(215, 236, 255, 170))

    img.alpha_composite(layer)


def _draw_header(draw: ImageDraw.ImageDraw, cfg: DomainConfig, run_date: date, width: int) -> None:
    title_font = _load_font(88)
    date_font = _load_font(45)
    line_color = (120, 160, 220, 255)

    title = cfg.display_name
    date_text = f"{run_date.year}年{run_date.month}月{run_date.day}日"

    tb = draw.textbbox((0, 0), title, font=title_font)
    title_w = tb[2] - tb[0]
    draw.text(((width - title_w) // 2, 42), title, fill=(31, 94, 205, 255), font=title_font)

    db = draw.textbbox((0, 0), date_text, font=date_font)
    date_w = db[2] - db[0]
    draw.text(((width - date_w) // 2, 154), date_text, fill=(38, 76, 146, 255), font=date_font)

    line_y = 214
    center = width // 2
    line_len = max(200, int(width * 0.22))
    gap = max(115, int(width * 0.11))
    draw.line((center - gap - line_len, line_y, center - gap, line_y), fill=line_color, width=3)
    draw.line((center + gap, line_y, center + gap + line_len, line_y), fill=line_color, width=3)


def _draw_tag_chips(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    tags: Iterable[str],
    base_color: tuple[int, int, int],
    max_width: int,
    chip_size: int,
) -> None:
    cursor_x = x
    chip_bg = _mix(base_color, (255, 255, 255), 0.30)

    for tag in tags:
        text = str(tag).strip()
        if not text:
            continue
        chip_font = _pick_text_font(text, chip_size, role="chip")
        bbox = draw.textbbox((0, 0), text, font=chip_font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        cw = tw + int(22 * chip_size / 24)
        ch = th + int(12 * chip_size / 24)

        if cursor_x + cw > x + max_width:
            break

        draw.rounded_rectangle(
            (cursor_x, y, cursor_x + cw, y + ch),
            radius=max(8, int(ch / 2.2)),
            fill=(*chip_bg, 255),
        )
        draw.text((cursor_x + int(10 * chip_size / 24), y + 3), text, fill=(255, 255, 255, 255), font=chip_font)
        cursor_x += cw + int(9 * chip_size / 24)


def _pick_icon_kind(card: Card) -> str:
    blob = f"{card.title} {' '.join(card.tags)} {card.summary}".lower()
    if "openai" in blob:
        return "openai"
    if "meta" in blob:
        return "meta"
    if "google" in blob:
        return "google"
    if "anthropic" in blob:
        return "anthropic"
    if "minimax" in blob:
        return "minimax"
    if "apple" in blob or "苹果" in blob:
        return "apple"
    if any(k in blob for k in ["财报", "营收", "融资", "增长", "涨", "首超", "trend"]):
        return "trend"
    if any(k in blob for k in ["芯片", "gpu", "nvidia", "算力"]):
        return "chip"
    if any(k in blob for k in ["robotaxi", "机器人", "自动驾驶", "车队"]):
        return "robot"
    if any(k in blob for k in ["发布", "新品", "活动", "launch", "event"]):
        return "megaphone"
    if any(k in blob for k in ["模型", "openai", "anthropic", "ai", "大模型"]):
        return "brain"
    return "spark"


def _draw_icon_badge(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    size: int,
    base_color: tuple[int, int, int],
) -> tuple[int, int, int]:
    _ = (draw, x, y, size)
    # Keep the return value for cutout effects in some glyphs, but remove any visible badge/border.
    return _mix(base_color, (255, 255, 255), 0.10)


def _draw_icon(draw: ImageDraw.ImageDraw, kind: str, x: int, y: int, size: int, base_color: tuple[int, int, int]) -> None:
    plate = _draw_icon_badge(draw, x, y, size, base_color)
    fg = (255, 255, 255, 245)
    accent = (238, 247, 255, 235)
    stroke = max(2, int(size * 0.06))

    def p(rx: float, ry: float) -> tuple[int, int]:
        return (x + int(size * rx), y + int(size * ry))

    if kind == "openai":
        ring = [p(0.50, 0.20), p(0.72, 0.32), p(0.72, 0.58), p(0.50, 0.78), p(0.28, 0.58), p(0.28, 0.32)]
        rr = max(3, int(size * 0.11))
        for cx, cy in ring:
            draw.ellipse((cx - rr, cy - rr, cx + rr, cy + rr), fill=fg)
        cut = max(3, int(size * 0.09))
        cx, cy = p(0.50, 0.50)
        draw.ellipse((cx - cut, cy - cut, cx + cut, cy + cut), fill=(*plate, 224))
        return

    if kind == "meta":
        draw.ellipse((p(0.18, 0.28), p(0.52, 0.72)), outline=fg, width=stroke)
        draw.ellipse((p(0.48, 0.28), p(0.82, 0.72)), outline=fg, width=stroke)
        return

    if kind == "google":
        f = _load_font(max(13, int(size * 0.56)))
        bbox = draw.textbbox((0, 0), "G", font=f)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        draw.text((x + (size - tw) // 2, y + (size - th) // 2 - 1), "G", fill=fg, font=f)
        return

    if kind == "anthropic":
        draw.polygon([p(0.50, 0.18), p(0.18, 0.82), p(0.32, 0.82), p(0.42, 0.62), p(0.58, 0.62), p(0.68, 0.82), p(0.82, 0.82)], fill=fg)
        draw.polygon([p(0.45, 0.50), p(0.55, 0.50), p(0.50, 0.38)], fill=accent)
        return

    if kind == "minimax":
        for rx, ry in [(0.31, 0.31), (0.69, 0.31), (0.31, 0.69), (0.69, 0.69)]:
            cx, cy = p(rx, ry)
            rr = max(3, int(size * 0.10))
            draw.ellipse((cx - rr, cy - rr, cx + rr, cy + rr), fill=fg)
        return

    if kind == "apple":
        draw.ellipse((p(0.30, 0.30), p(0.74, 0.80)), fill=fg)
        draw.ellipse((p(0.16, 0.36), p(0.56, 0.80)), fill=fg)
        draw.ellipse((p(0.65, 0.35), p(0.82, 0.53)), fill=(*plate, 224))
        draw.ellipse((p(0.56, 0.14), p(0.72, 0.30)), fill=accent)
        return

    if kind == "trend":
        bw = max(4, int(size * 0.13))
        gap = max(3, int(size * 0.08))
        base = y + int(size * 0.78)
        hs = [int(size * 0.16), int(size * 0.28), int(size * 0.42)]
        for i, bh in enumerate(hs):
            x0 = x + int(size * 0.16) + i * (bw + gap)
            draw.rounded_rectangle((x0, base - bh, x0 + bw, base), radius=2, fill=fg)
        draw.line((x + int(size * 0.14), y + int(size * 0.62), x + int(size * 0.80), y + int(size * 0.28)), fill=fg, width=stroke)
        draw.polygon([p(0.80, 0.28), p(0.68, 0.27), p(0.73, 0.38)], fill=fg)
        return

    if kind == "chip":
        x0, y0 = p(0.24, 0.24)
        x1, y1 = p(0.76, 0.76)
        draw.rounded_rectangle((x0, y0, x1, y1), radius=max(3, int(size * 0.08)), fill=fg)
        ix0, iy0 = p(0.37, 0.37)
        ix1, iy1 = p(0.63, 0.63)
        draw.rectangle((ix0, iy0, ix1, iy1), fill=(*plate, 224))
        for i in range(4):
            px = x + int(size * (0.31 + i * 0.12))
            draw.line((px, y + int(size * 0.09), px, y0), fill=fg, width=max(1, int(stroke * 0.65)))
            draw.line((px, y1, px, y + int(size * 0.91)), fill=fg, width=max(1, int(stroke * 0.65)))
            py = y + int(size * (0.31 + i * 0.12))
            draw.line((x + int(size * 0.09), py, x0, py), fill=fg, width=max(1, int(stroke * 0.65)))
            draw.line((x1, py, x + int(size * 0.91), py), fill=fg, width=max(1, int(stroke * 0.65)))
        return

    if kind == "robot":
        draw.rounded_rectangle((p(0.20, 0.23), p(0.80, 0.68)), radius=max(4, int(size * 0.12)), fill=fg)
        eye_r = max(2, int(size * 0.06))
        for ex in [0.42, 0.58]:
            cx, cy = p(ex, 0.44)
            draw.ellipse((cx - eye_r, cy - eye_r, cx + eye_r, cy + eye_r), fill=(*plate, 224))
        draw.line((x + int(size * 0.50), y + int(size * 0.10), x + int(size * 0.50), y + int(size * 0.23)), fill=fg, width=max(2, int(stroke * 0.7)))
        dot_r = max(2, int(size * 0.05))
        cx, cy = p(0.50, 0.08)
        draw.ellipse((cx - dot_r, cy - dot_r, cx + dot_r, cy + dot_r), fill=fg)
        draw.rounded_rectangle((p(0.33, 0.74), p(0.67, 0.82)), radius=4, fill=fg)
        return

    if kind == "megaphone":
        draw.polygon([p(0.20, 0.45), p(0.61, 0.26), p(0.61, 0.72)], fill=fg)
        draw.rounded_rectangle((p(0.14, 0.45), p(0.30, 0.60)), radius=3, fill=accent)
        draw.line((x + int(size * 0.68), y + int(size * 0.36), x + int(size * 0.84), y + int(size * 0.28)), fill=fg, width=max(2, int(stroke * 0.7)))
        draw.line((x + int(size * 0.68), y + int(size * 0.62), x + int(size * 0.84), y + int(size * 0.72)), fill=fg, width=max(2, int(stroke * 0.7)))
        return

    if kind == "brain":
        nodes = [p(0.32, 0.40), p(0.50, 0.28), p(0.68, 0.40), p(0.64, 0.61), p(0.38, 0.61)]
        for cx, cy in nodes:
            rr = max(2, int(size * 0.08))
            draw.ellipse((cx - rr, cy - rr, cx + rr, cy + rr), fill=fg)
        links = [(0, 1), (1, 2), (2, 3), (3, 4), (4, 0), (1, 4)]
        for a, b in links:
            draw.line((nodes[a][0], nodes[a][1], nodes[b][0], nodes[b][1]), fill=accent, width=max(2, int(stroke * 0.6)))
        return

    # spark fallback
    cx, cy = p(0.50, 0.50)
    outer = int(size * 0.28)
    inner = int(size * 0.12)
    star = [
        (cx, cy - outer),
        (cx + inner, cy - inner),
        (cx + outer, cy),
        (cx + inner, cy + inner),
        (cx, cy + outer),
        (cx - inner, cy + inner),
        (cx - outer, cy),
        (cx - inner, cy - inner),
    ]
    draw.polygon(star, fill=fg)


def _build_rounded_alpha_mask(width: int, height: int, radius: int) -> Image.Image:
    upscale = 4
    mask_large = Image.new("L", (width * upscale, height * upscale), 0)
    d = ImageDraw.Draw(mask_large)
    d.rounded_rectangle(
        (0, 0, width * upscale - 1, height * upscale - 1),
        radius=max(1, radius * upscale),
        fill=255,
    )
    return mask_large.resize((width, height), Image.Resampling.LANCZOS)


def _draw_card(
    canvas: Image.Image,
    card: Card,
    color_hex: str,
    x: int,
    y: int,
    w: int,
    h: int,
    icon_cache_dir: Path | None = None,
    icon_timeout_sec: int = 6,
    icon_proxy_url: str = "",
) -> None:
    radius = max(16, int(min(w, h) * 0.10))
    inner_pad = max(12, int(min(w, h) * 0.07))
    color_rgb = _hex_to_rgb(color_hex)

    if h > w * 1.2:
        top_ratio = 0.45
    elif w > h * 1.35:
        top_ratio = 0.58
    else:
        top_ratio = 0.53
    top_h = int(h * top_ratio)

    card_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(card_layer)

    draw.rectangle((0, 0, w, h), fill=(255, 255, 255, 255))

    # Colored top with subtle vertical gradient.
    steps = max(8, top_h)
    for i in range(steps):
        ratio = i / max(1, steps - 1)
        c = _mix(color_rgb, (255, 255, 255), ratio * 0.16)
        draw.line((1, i, w - 1, i), fill=(*c, 255))

    draw.rectangle((1, top_h, w - 1, h - 1), fill=(255, 255, 255, 255))

    scale = min(w / 320.0, h / 350.0)
    scale = max(0.70, min(1.45, scale))

    chip_size = max(13, int(23 * scale))

    title_max_lines = 3 if w >= 250 else 2
    title_lines, title_font = _fit_lines_and_font(
        draw,
        card.title,
        max_width=w - inner_pad * 2 - int(64 * scale),
        max_lines=title_max_lines,
        base_size=max(20, int(34 * scale)),
        min_size=max(16, int(24 * scale)),
        role="title",
    )
    ty = inner_pad
    title_font_size = int(getattr(title_font, "size", max(20, int(34 * scale))))
    title_line_gap = max(22, int(title_font_size * 1.18))
    for line in title_lines:
        line_font = _pick_text_font(line, title_font_size, role="title")
        draw.text((inner_pad, ty), line, fill=(255, 255, 255, 255), font=line_font)
        ty += title_line_gap

    icon_size = max(34, int(70 * scale))
    icon_kind = (card.icon_kind or "").strip().lower()
    if icon_kind not in ALLOWED_ICON_KINDS:
        icon_kind = _pick_icon_kind(card)
    icon_x = w - inner_pad - icon_size
    icon_y = inner_pad - 2
    drew_remote_icon = False
    if card.icon_url and icon_cache_dir is not None:
        remote_icon = _load_remote_icon(
            card.icon_url,
            icon_cache_dir,
            icon_size,
            icon_timeout_sec,
            icon_proxy_url,
        )
        if remote_icon is not None:
            card_layer.alpha_composite(remote_icon, (icon_x, icon_y))
            drew_remote_icon = True

    if not drew_remote_icon:
        _draw_icon(draw, icon_kind, icon_x, icon_y, icon_size, color_rgb)

    chip_y = max(int(top_h * 0.68), ty + 6)
    _draw_tag_chips(draw, inner_pad, chip_y, card.tags, color_rgb, w - inner_pad * 2, chip_size)

    summary_max_lines = 6 if h > w * 1.35 else 5 if h > w * 1.10 else 4
    if w < 280:
        summary_max_lines = min(7, summary_max_lines + 1)
    summary_lines, body_font = _fit_lines_and_font(
        draw,
        card.summary,
        max_width=w - inner_pad * 2,
        max_lines=summary_max_lines,
        base_size=max(17, int(26 * scale)),
        min_size=max(13, int(20 * scale)),
        role="body",
    )
    body_font_size = int(getattr(body_font, "size", max(17, int(26 * scale))))
    sy = top_h + max(10, int(16 * scale))
    body_gap = max(16, int(body_font_size * 1.38))
    for line in summary_lines:
        line_font = _pick_text_font(line, body_font_size, role="body")
        draw.text((inner_pad, sy), line, fill=(28, 34, 44, 255), font=line_font)
        sy += body_gap

    footer = f"{card.source_label} | {card.date_label}"
    footer_base_size = max(12, int(20 * scale))
    footer_min_size = max(9, int(14 * scale))
    footer_max_w = max(72, w - inner_pad * 2)
    footer_text, footer_font = _fit_text_and_font(
        draw,
        footer,
        max_width=footer_max_w,
        base_size=footer_base_size,
        min_size=footer_min_size,
        role="footer",
    )
    fb = draw.textbbox((0, 0), footer_text, font=footer_font)
    fw = fb[2] - fb[0]
    draw.text(
        (w - inner_pad - fw, h - max(18, int(38 * scale))),
        footer_text,
        fill=(138, 145, 160, 255),
        font=footer_font,
    )

    draw.rounded_rectangle(
        (1, 1, w - 2, h - 2),
        radius=max(8, radius - 1),
        outline=(180, 200, 230, 220),
        width=2,
    )

    # Use supersampled alpha mask to avoid corner halo/jagged artifacts.
    mask = _build_rounded_alpha_mask(w, h, radius)
    card_layer.putalpha(mask)

    canvas.alpha_composite(card_layer, (x, y))


def _resolve_layout(
    count: int,
    pattern_index: int,
    area_x: int,
    area_y: int,
    area_w: int,
    area_h: int,
    gap: int,
) -> list[tuple[int, int, int, int]]:
    pattern = LAYOUT_PATTERNS[pattern_index % len(LAYOUT_PATTERNS)]

    return _grid_rects_to_pixels(
        pattern[:count],
        area_x,
        area_y,
        area_w,
        area_h,
        gap,
        grid_cols=12,
        grid_rows=12,
    )


def _grid_rects_to_pixels(
    grid_rects: Sequence[tuple[int, int, int, int]],
    area_x: int,
    area_y: int,
    area_w: int,
    area_h: int,
    gap: int,
    grid_cols: int,
    grid_rows: int,
) -> list[tuple[int, int, int, int]]:
    usable_w = area_w - gap * max(0, grid_cols - 1)
    usable_h = area_h - gap * max(0, grid_rows - 1)
    cell_w = usable_w / float(max(1, grid_cols))
    cell_h = usable_h / float(max(1, grid_rows))

    rects: list[tuple[int, int, int, int]] = []
    for x, y, w, h in grid_rects:
        rx = int(round(area_x + x * (cell_w + gap)))
        ry = int(round(area_y + y * (cell_h + gap)))
        rw = int(round(w * cell_w + max(0, w - 1) * gap))
        rh = int(round(h * cell_h + max(0, h - 1) * gap))
        rects.append((rx, ry, rw, rh))
    return rects


def _distribute_units(
    total: int,
    parts: int,
    min_each: int,
    weights: Sequence[float],
    max_each: int | None = None,
) -> list[int] | None:
    if parts <= 0:
        return []
    if min_each * parts > total:
        return None

    out = [min_each for _ in range(parts)]
    remaining = total - min_each * parts
    if remaining <= 0:
        return out

    weight_list = [float(weights[i]) if i < len(weights) else 0.0 for i in range(parts)]
    if len({round(v, 6) for v in weight_list}) <= 1:
        order = list(range(parts))
    else:
        order = sorted(range(parts), key=lambda i: weight_list[i], reverse=True)

    while remaining > 0:
        moved = False
        for idx in order:
            if remaining <= 0:
                break
            if max_each is not None and out[idx] >= max_each:
                continue
            out[idx] += 1
            remaining -= 1
            moved = True
        if not moved:
            break

    if remaining > 0:
        return None
    return out


def _distribute_row_card_counts(total_cards: int, row_count: int, max_per_row: int) -> list[int]:
    if total_cards <= 0 or row_count <= 0:
        return []

    counts: list[int] = []
    remaining = total_cards
    for i in range(row_count):
        rows_left = row_count - i
        min_for_row = max(1, remaining - (rows_left - 1) * max_per_row)
        max_for_row = min(max_per_row, remaining - (rows_left - 1))
        target = int(round(remaining / max(1, rows_left)))
        chosen = max(min_for_row, min(max_for_row, target))
        counts.append(chosen)
        remaining -= chosen
    return counts


def _resolve_smart_layout(
    count: int,
    area_x: int,
    area_y: int,
    area_w: int,
    area_h: int,
    gap: int,
    scores: Sequence[float],
    text_weights: Sequence[float],
    primary_emphasis: bool,
) -> list[tuple[int, int, int, int]] | None:
    if count <= 0:
        return []

    cols = 12
    rows = 12
    rects_grid: list[tuple[int, int, int, int] | None] = [None for _ in range(count)]

    score_norm = _normalize_values(list(scores), default=0.5)
    text_norm = _normalize_values(list(text_weights), default=0.5)

    hero_idx = max(range(count), key=lambda i: score_norm[i]) if primary_emphasis else 0
    if count == 1:
        rects_grid[hero_idx] = (0, 0, cols, rows)
        return _grid_rects_to_pixels(
            [r for r in rects_grid if r is not None],
            area_x,
            area_y,
            area_w,
            area_h,
            gap,
            grid_cols=cols,
            grid_rows=rows,
        )

    if count == 2:
        hero_w, hero_h = 12, 7
    elif count == 3:
        hero_w, hero_h = 8, 6
    elif count <= 6:
        hero_w, hero_h = 8, 5
    else:
        hero_w, hero_h = 8, 4

    if text_norm[hero_idx] >= 0.80 and count <= 6:
        hero_h = min(rows - 3, hero_h + 1)

    rects_grid[hero_idx] = (0, 0, hero_w, hero_h)
    other_indices = [i for i in range(count) if i != hero_idx]
    other_indices.sort(key=lambda i: (text_norm[i], score_norm[i]), reverse=True)

    right_w = cols - hero_w
    top_cards = 0
    if right_w > 0 and other_indices:
        top_cards = 1 if count <= 4 else 2
        top_cards = min(top_cards, len(other_indices))
        while top_cards > 1 and hero_h < top_cards * 2:
            top_cards -= 1

    top_indices = other_indices[:top_cards]
    bottom_indices = other_indices[top_cards:]

    if top_indices:
        top_weights = [text_norm[i] for i in top_indices]
        min_top_h = hero_h if len(top_indices) == 1 else 2
        top_heights = _distribute_units(
            total=hero_h,
            parts=len(top_indices),
            min_each=min_top_h,
            weights=top_weights,
        )
        if top_heights is None:
            return None
        cursor_y = 0
        for card_idx, block_h in zip(top_indices, top_heights):
            rects_grid[card_idx] = (hero_w, cursor_y, right_w, block_h)
            cursor_y += block_h

    bottom_h = rows - hero_h
    if bottom_indices:
        if bottom_h <= 0:
            return None

        max_cards_per_row = 3
        row_count = (len(bottom_indices) + max_cards_per_row - 1) // max_cards_per_row
        row_count = max(1, min(row_count, max(1, bottom_h // 2)))
        while row_count * max_cards_per_row < len(bottom_indices):
            row_count += 1
            if row_count > bottom_h:
                return None

        row_cards = _distribute_row_card_counts(
            total_cards=len(bottom_indices),
            row_count=row_count,
            max_per_row=max_cards_per_row,
        )
        if not row_cards:
            return None

        min_row_h = 2 if bottom_h >= row_count * 2 else 1
        row_weights: list[float] = []
        offset = 0
        for c in row_cards:
            segment = bottom_indices[offset : offset + c]
            offset += c
            if not segment:
                row_weights.append(0.0)
                continue
            row_weights.append(sum(text_norm[i] for i in segment) / float(len(segment)))

        row_heights = _distribute_units(
            total=bottom_h,
            parts=row_count,
            min_each=min_row_h,
            weights=row_weights,
            max_each=4 if min_row_h == 2 else None,
        )
        if row_heights is None:
            row_heights = _distribute_units(
                total=bottom_h,
                parts=row_count,
                min_each=min_row_h,
                weights=row_weights,
            )
        if row_heights is None:
            return None

        y_cursor = hero_h
        offset = 0
        for row_idx, cards_in_row in enumerate(row_cards):
            row_h = row_heights[row_idx]
            row_segment = bottom_indices[offset : offset + cards_in_row]
            offset += cards_in_row

            if cards_in_row <= 0:
                continue
            if cards_in_row == 1:
                spans = [12]
            else:
                spans = _distribute_units(
                    total=12,
                    parts=cards_in_row,
                    min_each=4,
                    weights=[text_norm[i] for i in row_segment],
                    max_each=7,
                )
                if spans is None:
                    spans = _distribute_units(
                        total=12,
                        parts=cards_in_row,
                        min_each=4,
                        weights=[text_norm[i] for i in row_segment],
                    )
            if spans is None:
                return None

            x_cursor = 0
            for card_idx, block_w in zip(row_segment, spans):
                rects_grid[card_idx] = (x_cursor, y_cursor, block_w, row_h)
                x_cursor += block_w
            if x_cursor != cols:
                return None

            y_cursor += row_h
        if y_cursor != rows:
            return None

    if any(r is None for r in rects_grid):
        return None

    return _grid_rects_to_pixels(
        [r for r in rects_grid if r is not None],
        area_x,
        area_y,
        area_w,
        area_h,
        gap,
        grid_cols=cols,
        grid_rows=rows,
    )


def _enforce_primary_emphasis(
    rects: list[tuple[int, int, int, int]],
    enabled: bool,
    preferred_idx: int = 0,
) -> list[tuple[int, int, int, int]]:
    if not enabled or len(rects) < 2:
        return rects

    preferred_idx = max(0, min(preferred_idx, len(rects) - 1))
    areas = [w * h for (_, _, w, h) in rects]
    largest_idx = max(range(len(areas)), key=lambda i: areas[i])
    if largest_idx == preferred_idx:
        return rects

    adjusted = list(rects)
    adjusted[preferred_idx], adjusted[largest_idx] = adjusted[largest_idx], adjusted[preferred_idx]
    return adjusted


def plan_card_layout(
    cfg: DomainConfig,
    run_date: date,
    count: int,
    cards: Sequence[Card] | None = None,
    layout_hints: Sequence[dict[str, float]] | None = None,
) -> list[tuple[int, int, int, int]]:
    render_cfg = cfg.render
    width = int(render_cfg.get("width", 1240))
    height = int(render_cfg.get("height", 1920))
    margin = int(render_cfg.get("margin", 48))
    gap = int(render_cfg.get("gap", 14))
    header_h = int(render_cfg.get("header_height", 230))
    footer_h = int(render_cfg.get("footer_height", 48))

    card_area_x = margin
    card_area_y = header_h
    card_area_w = width - margin * 2
    card_area_h = height - header_h - footer_h - margin

    card_count = min(max(0, count), MAX_LAYOUT_CARDS)
    primary_emphasis = bool(render_cfg.get("primary_card_emphasis", True))
    layout_mode = str(render_cfg.get("layout_mode", "smart")).strip().lower()
    scores, text_weights = _collect_layout_signals(card_count, cards=cards, layout_hints=layout_hints)
    hero_idx = max(range(card_count), key=lambda i: scores[i]) if primary_emphasis and card_count > 0 else 0

    if layout_mode in {"smart", "mixed", "auto"}:
        smart_rects = _resolve_smart_layout(
            card_count,
            card_area_x,
            card_area_y,
            card_area_w,
            card_area_h,
            gap,
            scores,
            text_weights,
            primary_emphasis=primary_emphasis,
        )
        if smart_rects is not None:
            return smart_rects

    pattern_index = run_date.toordinal() % len(LAYOUT_PATTERNS)
    rects = _resolve_layout(card_count, pattern_index, card_area_x, card_area_y, card_area_w, card_area_h, gap)
    rects = _enforce_primary_emphasis(rects, primary_emphasis, preferred_idx=hero_idx)
    return rects


def render_infographic(
    cards: list[Card],
    cfg: DomainConfig,
    run_date: date,
    output_dir: str | Path | None = None,
    layout_hints: Sequence[dict[str, float]] | None = None,
) -> str:
    render_cfg = cfg.render
    width = int(render_cfg.get("width", 1240))
    height = int(render_cfg.get("height", 1920))
    margin = int(render_cfg.get("margin", 48))
    gap = int(render_cfg.get("gap", 14))
    header_h = int(render_cfg.get("header_height", 230))
    footer_h = int(render_cfg.get("footer_height", 48))
    palette = list(render_cfg.get("card_palette", []))
    if not palette:
        palette = ["#2B6BE4"] * max(1, len(cards))

    out_root = Path(output_dir or render_cfg.get("output_dir", "output"))
    out_root.mkdir(parents=True, exist_ok=True)
    icon_cache_dir_raw = render_cfg.get("icon_cache_dir", "icon_cache")
    icon_cache_dir = Path(str(icon_cache_dir_raw))
    if not icon_cache_dir.is_absolute():
        icon_cache_dir = out_root / icon_cache_dir
    icon_cache_dir.mkdir(parents=True, exist_ok=True)
    icon_fetch_timeout_sec = int(render_cfg.get("icon_fetch_timeout_sec", 6))
    icon_fetch_proxy = str(render_cfg.get("icon_fetch_proxy", "")).strip() or os.getenv("ICON_FETCH_PROXY", "").strip()

    img = Image.new("RGBA", (width, height), color=(240, 245, 255, 255))
    _draw_gradient_background(
        img,
        str(render_cfg.get("background", {}).get("start", "#eef6ff")),
        str(render_cfg.get("background", {}).get("end", "#dae8ff")),
    )
    _draw_background_decorations(img)
    draw = ImageDraw.Draw(img)

    _draw_header(draw, cfg, run_date, width)

    rects = plan_card_layout(
        cfg,
        run_date,
        len(cards),
        cards=cards,
        layout_hints=layout_hints,
    )

    for idx, (x, y, w, h) in enumerate(rects):
        color = palette[idx % len(palette)]
        _draw_card(
            img,
            cards[idx],
            color,
            x,
            y,
            w,
            h,
            icon_cache_dir=icon_cache_dir,
            icon_timeout_sec=icon_fetch_timeout_sec,
            icon_proxy_url=icon_fetch_proxy,
        )

    watermark_font = _load_font(22)
    watermark = "Generated by Daily News Digest"
    wb = draw.textbbox((0, 0), watermark, font=watermark_font)
    ww = wb[2] - wb[0]
    draw.text(((width - ww) // 2, height - 34), watermark, fill=(150, 160, 175, 255), font=watermark_font)

    image_path = out_root / f"{run_date.isoformat()}-{cfg.domain}.png"
    img.convert("RGB").save(image_path)
    return str(image_path)
