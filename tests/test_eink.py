from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path

from PIL import Image

from app import create_app
from app.config import Settings
from app.eink import (HEIGHT, RAW_SIZE, WIDTH, EInkFrameService, composite_on_white,
                      image_to_rgb565_le, render_eink_bundle, render_eink_image)


def fixture_state(warning: str | None = None) -> dict:
    return {
        "profile": {"name": "E-ink Test"},
        "server_time": "2026-09-26T08:30:00-04:00",
        "freshness_seconds": 45,
        "warning": warning,
        "entries": [
            {
                "mode": "bus", "location_name": "Georgia Av NW + Irving St NW",
                "route": "D40", "destination": "North to Silver Spring",
                "last": {"display": "12m ago"},
                "arrivals": [
                    {"display": "3m", "destination": "North to Silver Spring", "state": "live"},
                    {"display": "18m?", "destination": "North to Silver Spring", "state": "stale"},
                ],
            },
            {
                "mode": "rail", "location_name": "Columbia Heights",
                "route": "GR", "destination": "Branch Ave", "last": None,
                "arrivals": [{"display": "6m", "destination": "Branch Ave", "state": "live"}],
            },
        ],
    }


def test_eink_preview_and_raw_dimensions_are_exact():
    image = render_eink_image(fixture_state())
    bundle = render_eink_bundle(fixture_state())

    assert image.size == (600, 800)
    assert image.mode == "RGB"
    with Image.open(BytesIO(bundle.png)) as preview:
        assert preview.size == (600, 800)
        assert preview.mode == "RGB"
    assert len(bundle.rgb565) == 960_000 == RAW_SIZE
    assert bundle.png == render_eink_bundle(fixture_state()).png


def test_rgb565_is_little_endian_for_known_colors():
    image = Image.new("RGB", (WIDTH, HEIGHT), "white")
    colors = [(255, 255, 255), (0, 0, 0), (255, 0, 0), (0, 255, 0), (0, 0, 255)]
    for x, color in enumerate(colors):
        image.putpixel((x, 0), color)

    raw = image_to_rgb565_le(image)
    assert raw[:10] == bytes.fromhex("ffff 0000 00f8 e007 1f00")


def test_transparency_is_composited_onto_white():
    transparent = Image.new("RGBA", (WIDTH, HEIGHT), (255, 0, 0, 0))
    composited = composite_on_white(transparent)
    assert composited.getpixel((0, 0)) == (255, 255, 255)
    assert image_to_rgb565_le(transparent)[:2] == b"\xff\xff"


def test_warning_and_stale_state_still_render_valid_frame():
    bundle = render_eink_bundle(fixture_state("WMATA unavailable — showing last usable data"))
    assert len(bundle.rgb565) == RAW_SIZE
    with Image.open(BytesIO(bundle.png)) as preview:
        assert preview.size == (WIDTH, HEIGHT)
        # The warning banner is black and therefore visible without color.
        assert preview.getpixel((30, 130)) == (0, 0, 0)


def test_atomic_service_preserves_last_good_frame_and_concurrent_reads(tmp_path):
    service = EInkFrameService(str(tmp_path / "eink"), refresh_seconds=0)
    first = service.get_or_generate(fixture_state, force=True)
    assert service.png_path.read_bytes() == first.png
    assert service.raw_path.read_bytes() == first.rgb565

    def fail():
        raise RuntimeError("fixture render failure")

    fallback = service.get_or_generate(fail, force=True)
    assert fallback.png == first.png
    assert fallback.rgb565 == first.rgb565
    assert service.png_path.read_bytes() == first.png
    assert service.raw_path.read_bytes() == first.rgb565

    cached = EInkFrameService(str(tmp_path / "eink"), refresh_seconds=3600)
    with ThreadPoolExecutor(max_workers=8) as pool:
        sizes = list(pool.map(lambda _: len(cached.get_or_generate(fixture_state).rgb565), range(16)))
    assert sizes == [RAW_SIZE] * 16


def settings(database_path: Path, frame_dir: Path) -> Settings:
    return Settings("", "127.0.0.1", 8080, str(database_path), "", "",
                    "INFO", 20, False, "test-secret", eink_frame_dir=str(frame_dir))


def test_eink_endpoints_use_cached_server_state_and_required_headers(tmp_path):
    app = create_app({
        "TESTING": True,
        "SETTINGS": settings(tmp_path / "app.sqlite3", tmp_path / "frames"),
    })
    browser = app.test_client()

    preview = browser.get("/eink/dashboard.png")
    assert preview.status_code == 200
    assert preview.content_type == "image/png"
    assert preview.headers["Cache-Control"] == "no-store"
    with Image.open(BytesIO(preview.data)) as image:
        assert image.size == (WIDTH, HEIGHT)

    raw = browser.get("/eink/dashboard.rgb565")
    assert raw.status_code == 200
    assert raw.content_type == "application/octet-stream"
    assert raw.headers["Content-Length"] == "960000"
    assert raw.headers["Cache-Control"] == "no-store"
    assert len(raw.data) == RAW_SIZE
