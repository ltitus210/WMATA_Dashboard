import json
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path

from PIL import Image

from app import create_app
from app.config import Settings
from app.eink import (HEIGHT, KINDLE_PW2_TARGET, RAW_SIZE, WIDTH, EInkFrameService,
                      composite_on_white, image_to_rgb565_le, render_eink_bundle,
                      render_eink_frame_set, render_eink_image)


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


def test_kindle_pw2_target_has_native_dimensions_and_raw_size():
    image = render_eink_image(fixture_state(), target=KINDLE_PW2_TARGET)
    bundle = render_eink_bundle(fixture_state(), target=KINDLE_PW2_TARGET)

    assert image.size == (758, 1024)
    with Image.open(BytesIO(bundle.png)) as preview:
        assert preview.size == (758, 1024)
        assert preview.mode == "RGB"
    assert len(bundle.rgb565) == 758 * 1024 * 2 == 1_552_384


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
    first_set = service.get_or_generate("home", fixture_state, force=True)
    first = first_set.pages[0]
    assert service.png_path("home").read_bytes() == first.png
    assert service.raw_path("home").read_bytes() == first.rgb565

    def fail():
        raise RuntimeError("fixture render failure")

    fallback = service.get_or_generate("home", fail, force=True).pages[0]
    assert fallback.png == first.png
    assert fallback.rgb565 == first.rgb565
    assert service.png_path("home").read_bytes() == first.png
    assert service.raw_path("home").read_bytes() == first.rgb565

    cached = EInkFrameService(str(tmp_path / "eink"), refresh_seconds=3600)
    with ThreadPoolExecutor(max_workers=8) as pool:
        sizes = list(pool.map(
            lambda _: len(cached.get_or_generate("home", fixture_state).pages[0].rgb565), range(16)
        ))
    assert sizes == [RAW_SIZE] * 16


def test_frame_set_paginates_every_configured_route(tmp_path):
    state = fixture_state()
    state["entries"] = [
        {
            "mode": "bus", "location_name": f"Stop {index}", "route": f"R{index}",
            "destination": f"Destination {index}", "last": None,
            "arrivals": [{"display": f"{index + 1}m", "destination": f"Destination {index}"}],
        }
        for index in range(13)
    ]
    frame_set = render_eink_frame_set(state, "home")
    assert len(frame_set.pages) == 3
    assert all(len(bundle.rgb565) == RAW_SIZE for bundle in frame_set.pages)
    assert len({bundle.png for bundle in frame_set.pages}) == 3

    service = EInkFrameService(str(tmp_path / "eink"), refresh_seconds=0)
    published = service.get_or_generate("home", lambda: state, force=True)
    manifest = json.loads(service.manifest_path("home").read_text())
    assert manifest["page_count"] == 3
    assert [page["png"] for page in manifest["pages"]] == [
        "dashboard.png", "dashboard-2.png", "dashboard-3.png"
    ]
    assert service.png_path("home", 3).read_bytes() == published.pages[2].png
    assert service.raw_path("home", 3).stat().st_size == RAW_SIZE


def settings(database_path: Path, frame_dir: Path) -> Settings:
    return Settings("", "127.0.0.1", 8080, str(database_path), "", "",
                    "INFO", 20, False, "test-secret", eink_frame_dir=str(frame_dir))


def test_eink_endpoints_use_cached_server_state_and_required_headers(tmp_path):
    app = create_app({
        "TESTING": True,
        "SETTINGS": settings(tmp_path / "app.sqlite3", tmp_path / "frames"),
    })
    browser = app.test_client()

    database = app.extensions["database"]
    profile = database.profile("home")
    now = "2026-09-26T08:30:00+00:00"
    for index in range(8):
        database.execute(
            """INSERT INTO entries(profile_id,position,mode,location_id,location_name,route,
               direction,destination,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (profile["id"], index, "bus", f"100{index:04d}", f"Stop {index}", f"R{index}",
             "0", f"Destination {index}", now, now),
        )

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

    index = browser.get("/eink/home/")
    assert index.status_code == 200
    assert b"Page 2 of 2" in index.data
    manifest = browser.get("/eink/home/manifest.json")
    assert manifest.status_code == 200
    assert manifest.headers["Cache-Control"] == "no-store"
    assert manifest.json == {
        "profile": "home", "target": "nook", "width": 600, "height": 800,
        "raw_size": 960_000, "page_count": 2,
        "pages": [
            {"page": 1, "png": "/eink/home/dashboard.png",
             "rgb565": "/eink/home/dashboard.rgb565"},
            {"page": 2, "png": "/eink/home/dashboard-2.png",
             "rgb565": "/eink/home/dashboard-2.rgb565"},
        ],
    }
    assert browser.get("/eink/home/dashboard-2.png").status_code == 200
    explicit_nook = browser.get("/eink/home/nook/dashboard.png")
    with Image.open(BytesIO(explicit_nook.data)) as image:
        assert image.size == (600, 800)
    raw_page_two = browser.get("/eink/home/dashboard-2.rgb565")
    assert raw_page_two.status_code == 200
    assert len(raw_page_two.data) == RAW_SIZE
    assert browser.get("/eink/home/dashboard-3.png").status_code == 404
    assert browser.get("/eink/missing/dashboard.png").status_code == 404

    pw2_manifest = browser.get("/eink/home/kindle-pw2/manifest.json")
    assert pw2_manifest.status_code == 200
    assert pw2_manifest.json["target"] == "kindle-pw2"
    assert pw2_manifest.json["width"] == 758
    assert pw2_manifest.json["height"] == 1024
    assert pw2_manifest.json["raw_size"] == 1_552_384
    assert pw2_manifest.json["pages"][0]["png"] == "/eink/home/kindle-pw2/dashboard.png"
    pw2_preview = browser.get("/eink/home/kindle-pw2/dashboard.png")
    with Image.open(BytesIO(pw2_preview.data)) as image:
        assert image.size == (758, 1024)
    pw2_raw = browser.get("/eink/home/kindle-pw2/dashboard.rgb565")
    assert pw2_raw.headers["Content-Length"] == "1552384"
    assert len(pw2_raw.data) == 1_552_384
    assert browser.get("/eink/home/unknown/dashboard.png").status_code == 404

    database.execute(
        "INSERT INTO profiles(slug,name,created_at,updated_at) VALUES(?,?,?,?)",
        ("office", "Office", now, now),
    )
    office_manifest = browser.get("/eink/office/manifest.json")
    assert office_manifest.status_code == 200
    assert office_manifest.json["profile"] == "office"
    assert office_manifest.json["page_count"] == 1

    admin = browser.get("/admin")
    assert b'/eink/home/' in admin.data
    assert b'/eink/home/dashboard.png' in admin.data
    assert b'/eink/home/dashboard.rgb565' in admin.data
    assert b'/eink/home/kindle-pw2/dashboard.png' in admin.data
    assert b'/eink/office/' in admin.data
    assert b'/eink/office/dashboard.png' in admin.data
    assert b'/eink/office/dashboard.rgb565' in admin.data
