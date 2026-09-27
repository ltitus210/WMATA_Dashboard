from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
from io import BytesIO
import logging
import os
from pathlib import Path
import tempfile
import threading
import time
from typing import Callable
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont


LOG = logging.getLogger(__name__)
WIDTH = 600
HEIGHT = 800
RAW_SIZE = WIDTH * HEIGHT * 2


@dataclass(frozen=True, slots=True)
class EInkFrameBundle:
    png: bytes
    rgb565: bytes
    generated_monotonic: float


def _font_candidates(configured: str, bold: bool) -> list[str]:
    candidates = [configured] if configured else []
    if bold:
        candidates.extend([
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
            "/System/Library/Fonts/Supplemental/Verdana Bold.ttf",
        ])
    else:
        candidates.extend([
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
            "/System/Library/Fonts/Supplemental/Verdana.ttf",
        ])
    return candidates


@lru_cache(maxsize=32)
def _font(configured: str, size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in _font_candidates(configured, bold):
        if candidate and Path(candidate).is_file():
            return ImageFont.truetype(candidate, size=size)
    LOG.warning("E-ink display TrueType font unavailable; using Pillow fallback font")
    return ImageFont.load_default(size=size)


def _text_width(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    box = draw.textbbox((0, 0), text, font=font)
    return int(box[2] - box[0])


def _fit_text(draw: ImageDraw.ImageDraw, text: str, font, width: int) -> str:
    text = " ".join(str(text or "").split())
    if _text_width(draw, text, font) <= width:
        return text
    suffix = "…"
    while text and _text_width(draw, text + suffix, font) > width:
        text = text[:-1]
    return text.rstrip() + suffix if text else suffix


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font, width: int, max_lines: int = 2) -> list[str]:
    words = str(text or "").split()
    lines: list[str] = []
    current = ""
    for word in words:
        proposed = f"{current} {word}".strip()
        if current and _text_width(draw, proposed, font) > width:
            lines.append(current)
            current = word
            if len(lines) == max_lines:
                break
        else:
            current = proposed
    if len(lines) < max_lines and current:
        lines.append(current)
    consumed = " ".join(lines)
    if len(consumed) < len(" ".join(words)) and lines:
        lines[-1] = _fit_text(draw, lines[-1] + "…", font, width)
    return lines


def composite_on_white(image: Image.Image) -> Image.Image:
    """Return a fully opaque RGB image, replacing transparency with white."""
    rgba = image.convert("RGBA")
    background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
    background.alpha_composite(rgba)
    return background.convert("RGB")


def image_to_rgb565_le(image: Image.Image) -> bytes:
    """Pack a 600x800 image as headerless, top-to-bottom little-endian RGB565."""
    rgb = composite_on_white(image)
    if rgb.size != (WIDTH, HEIGHT):
        raise ValueError(f"E-ink display frame must be {WIDTH}x{HEIGHT}, got {rgb.width}x{rgb.height}")
    output = bytearray(RAW_SIZE)
    offset = 0
    pixels = rgb.get_flattened_data() if hasattr(rgb, "get_flattened_data") else rgb.getdata()
    for red, green, blue in pixels:
        value = ((red >> 3) << 11) | ((green >> 2) << 5) | (blue >> 3)
        output[offset] = value & 0xFF
        output[offset + 1] = (value >> 8) & 0xFF
        offset += 2
    return bytes(output)


def _state_time(state: dict) -> datetime:
    try:
        return datetime.fromisoformat(str(state.get("server_time", "")).replace("Z", "+00:00"))
    except ValueError:
        return datetime.now().astimezone()


def render_eink_image(state: dict, font_path: str = "") -> Image.Image:
    """Render deterministic dashboard state to the default 600x800 e-ink canvas."""
    image = Image.new("RGBA", (WIDTH, HEIGHT), (255, 255, 255, 255))
    draw = ImageDraw.Draw(image)
    regular = {size: _font(font_path, size) for size in (14, 16, 18, 20, 22, 24)}
    bold = {size: _font(font_path, size, True) for size in (18, 22, 30, 38, 42)}
    black = (0, 0, 0, 255)
    white = (255, 255, 255, 255)
    margin = 28

    profile = state.get("profile") or {}
    now = _state_time(state)
    freshness = max(0, int(state.get("freshness_seconds") or 0))
    data_time = now - timedelta(seconds=freshness)

    draw.text((margin, 22), "WMATA DEPARTURES", font=bold[18], fill=black)
    title = _fit_text(draw, str(profile.get("name") or "Transit"), bold[42], 350)
    draw.text((margin, 48), title, font=bold[42], fill=black)
    draw.text((WIDTH - margin, 30), now.strftime("%-I:%M %p"), font=bold[30], fill=black, anchor="ra")
    draw.text((WIDTH - margin, 68), now.strftime("%a %b %-d"), font=regular[18], fill=black, anchor="ra")
    draw.rectangle((margin, 108, WIDTH - margin, 113), fill=black)

    cursor_y = 128
    warning = str(state.get("warning") or "").strip()
    if warning:
        warning_height = 76
        draw.rectangle((margin, cursor_y, WIDTH - margin, cursor_y + warning_height), fill=black)
        draw.text((margin + 14, cursor_y + 9), "! DATA WARNING", font=bold[18], fill=white)
        lines = _wrap_text(draw, warning, regular[16], WIDTH - (2 * margin) - 28, 2)
        for index, line in enumerate(lines):
            draw.text((margin + 14, cursor_y + 34 + index * 18), line, font=regular[16], fill=white)
        cursor_y += warning_height + 14

    footer_top = 742
    row_height = 100
    capacity = max(1, (footer_top - cursor_y) // row_height)
    entries = list(state.get("entries") or [])
    ranked = sorted(enumerate(entries), key=lambda pair: (not bool(pair[1].get("arrivals")), pair[0]))
    selected = [entry for _, entry in ranked[:capacity]]

    if not selected:
        draw.text((WIDTH // 2, 330), "NO ARRIVALS", font=bold[38], fill=black, anchor="ma")
        draw.text((WIDTH // 2, 380), "No routes are configured or available.",
                  font=regular[20], fill=black, anchor="ma")
    for entry in selected:
        stop = _fit_text(draw, str(entry.get("location_name") or "Unknown stop"), bold[22], WIDTH - 2 * margin)
        draw.text((margin, cursor_y), stop, font=bold[22], fill=black)

        route = str(entry.get("route") or ("RAIL" if entry.get("mode") == "rail" else "BUS"))
        draw.text((margin, cursor_y + 30), _fit_text(draw, route, bold[38], 100), font=bold[38], fill=black)
        destination = entry.get("destination")
        arrivals = list(entry.get("arrivals") or [])
        if not destination and arrivals:
            destination = arrivals[0].get("destination")
        destination = _fit_text(draw, str(destination or "All destinations"), bold[18], 270)
        draw.text((132, cursor_y + 32), destination, font=bold[18], fill=black)

        last = entry.get("last") or {}
        last_text = f"LAST {last.get('display')}" if last.get("display") else "LAST —"
        draw.text((132, cursor_y + 57), last_text, font=regular[14], fill=black)

        time_values = [str(item.get("display") or "") for item in arrivals[:3] if item.get("display")]
        time_text = "  ".join(time_values) if time_values else "NO ARRIVALS"
        time_font = bold[30] if time_values else bold[18]
        time_text = _fit_text(draw, time_text, time_font, 170)
        draw.text((WIDTH - margin, cursor_y + 34), time_text, font=time_font, fill=black, anchor="ra")
        draw.rectangle((margin, cursor_y + 91, WIDTH - margin, cursor_y + 93), fill=black)
        cursor_y += row_height

    hidden = max(0, len(entries) - len(selected))
    footer_note = f"+{hidden} ROUTE{'S' if hidden != 1 else ''} NOT SHOWN" if hidden else "TOP-TO-BOTTOM • 600 × 800"
    draw.rectangle((0, footer_top, WIDTH, HEIGHT), fill=black)
    draw.text((margin, footer_top + 11), f"LAST UPDATED {data_time.strftime('%-I:%M %p')}",
              font=bold[18], fill=white)
    draw.text((margin, footer_top + 36), f"DATA AGE {freshness}s", font=regular[16], fill=white)
    draw.text((WIDTH - margin, footer_top + 21), footer_note, font=regular[14], fill=white, anchor="ra")
    return composite_on_white(image)


def render_eink_bundle(state: dict, font_path: str = "") -> EInkFrameBundle:
    image = render_eink_image(state, font_path)
    buffer = BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    png = buffer.getvalue()
    raw = image_to_rgb565_le(image)
    if len(raw) != RAW_SIZE:
        raise ValueError(f"Invalid RGB565 frame size: {len(raw)}")
    return EInkFrameBundle(png, raw, time.monotonic())


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class EInkFrameService:
    """Thread-safe, atomic, last-good cache for PNG and RGB565 frames."""

    def __init__(self, output_dir: str, refresh_seconds: int = 30, font_path: str = ""):
        self.output_dir = Path(output_dir)
        self.refresh_seconds = max(0, int(refresh_seconds))
        self.font_path = font_path
        self.png_path = self.output_dir / "dashboard.png"
        self.raw_path = self.output_dir / "dashboard.rgb565"
        self._lock = threading.RLock()
        self._bundle: EInkFrameBundle | None = None

    def _load_last_good(self) -> EInkFrameBundle | None:
        try:
            png = self.png_path.read_bytes()
            raw = self.raw_path.read_bytes()
            if len(raw) != RAW_SIZE:
                return None
            with Image.open(BytesIO(png)) as image:
                if image.size != (WIDTH, HEIGHT):
                    return None
                image.verify()
            return EInkFrameBundle(png, raw, time.monotonic())
        except (OSError, ValueError):
            return None

    def get_or_generate(self, state_factory: Callable[[], dict], force: bool = False) -> EInkFrameBundle:
        bundle = self._bundle
        if bundle and not force and time.monotonic() - bundle.generated_monotonic < self.refresh_seconds:
            return bundle
        with self._lock:
            bundle = self._bundle
            if bundle and not force and time.monotonic() - bundle.generated_monotonic < self.refresh_seconds:
                return bundle
            if bundle is None:
                bundle = self._load_last_good()
                self._bundle = bundle
            try:
                candidate = render_eink_bundle(state_factory(), self.font_path)
                _atomic_write(self.raw_path, candidate.rgb565)
                _atomic_write(self.png_path, candidate.png)
                self._bundle = candidate
                return candidate
            except Exception:
                LOG.exception("E-ink frame generation failed; preserving last valid frame")
                if bundle is not None:
                    return bundle
                raise


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate E-ink PNG and RGB565 dashboard frames")
    parser.add_argument("--profile", help="Dashboard profile slug (default: WMATA_EINK_PROFILE)")
    parser.add_argument("--output-dir", help="Destination directory (default: WMATA_EINK_FRAME_DIR)")
    args = parser.parse_args()

    from . import create_app
    from .routes import dashboard_state

    app = create_app({"TESTING": True})
    settings = app.config["SETTINGS"]
    profile = args.profile or settings.eink_profile
    service = EInkFrameService(args.output_dir or settings.eink_frame_dir, 0, settings.eink_font)
    with app.app_context():
        bundle = service.get_or_generate(lambda: dashboard_state(profile), force=True)
    print(f"PNG {service.png_path} {len(bundle.png)} bytes sha256={sha256(bundle.png).hexdigest()}")
    print(f"RGB565 {service.raw_path} {len(bundle.rgb565)} bytes sha256={sha256(bundle.rgb565).hexdigest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
