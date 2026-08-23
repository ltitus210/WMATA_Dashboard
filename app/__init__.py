from __future__ import annotations

import logging
import os
import time

from flask import Flask

from .config import Settings
from .database import Database
from .polling import PollingManager
from .routes import bp
from .wmata.client import WMATAClient

VERSION = "1.0.0"


def create_app(overrides: dict | None = None) -> Flask:
    settings = Settings.from_env()
    app = Flask(__name__, instance_relative_config=True)
    app.config.update(
        SECRET_KEY=settings.secret_key,
        JSON_SORT_KEYS=False,
        SETTINGS=settings,
        STARTED_MONOTONIC=time.monotonic(),
        VERSION=VERSION,
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
    db = Database(settings.database_path)
    db.initialize()
    db.seed_default_profile()
    app.extensions["database"] = db
    stored_api_key = db.get_secret("wmata_api_key")
    app.extensions["wmata"] = WMATAClient(settings.api_key or stored_api_key, db)
    app.register_blueprint(bp)

    if settings.start_poller and not app.config.get("TESTING"):
        poller = PollingManager(app, db, app.extensions["wmata"], settings)
        poller.start()
        app.extensions["poller"] = poller
    return app
