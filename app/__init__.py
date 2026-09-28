from __future__ import annotations

import logging
import os
import signal
import time

from flask import Flask

from .config import Settings
from .database import Database
from .logging_manager import LogManager
from .polling import PollingManager
from .routes import bp
from .wmata.client import WMATAClient

VERSION = "1.2.0"


def shutdown_application(app: Flask) -> None:
    """Stop background work, flush logs, and terminate the server process."""
    poller = app.extensions.get("poller")
    if poller:
        poller.stop()
    logging.getLogger(__name__).info("Application shutdown requested from administration interface")
    logging.shutdown()
    os.kill(os.getpid(), signal.SIGTERM)


def create_app(overrides: dict | None = None) -> Flask:
    from .eink import EInkFrameService

    settings = Settings.from_env()
    app = Flask(__name__, instance_relative_config=True)
    app.config.update(
        SECRET_KEY=settings.secret_key,
        JSON_SORT_KEYS=False,
        SETTINGS=settings,
        STARTED_MONOTONIC=time.monotonic(),
        VERSION=VERSION,
        SHUTDOWN_HANDLER=shutdown_application,
    )
    if overrides:
        app.config.update(overrides)
        if "SETTINGS" in overrides:
            settings = overrides["SETTINGS"]

    os.makedirs(app.instance_path, exist_ok=True)
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    log_file = settings.log_file or str(os.path.join(os.path.dirname(settings.database_path), "logs", "wmata-dashboard.log"))
    log_manager = LogManager(log_file, settings.log_max_bytes,
                             settings.log_backup_count, settings.log_level)
    app.extensions["log_manager"] = log_manager
    db = Database(settings.database_path)
    db.initialize()
    db.seed_default_profile()
    app.extensions["database"] = db
    stored_api_key = db.get_secret("wmata_api_key")
    app.extensions["wmata"] = WMATAClient(settings.api_key or stored_api_key, db)
    app.extensions["eink_frames"] = EInkFrameService(
        settings.eink_frame_dir, settings.eink_refresh_seconds, settings.eink_font
    )
    app.register_blueprint(bp)

    if settings.start_poller and not app.config.get("TESTING"):
        poller = PollingManager(app, db, app.extensions["wmata"], settings)
        poller.start()
        app.extensions["poller"] = poller
    return app
