from pathlib import Path

from app import create_app
from app.config import Settings


def settings(database_path: Path, environment_key: str = "") -> Settings:
    return Settings(environment_key, "127.0.0.1", 8080, str(database_path), "", "",
                    "INFO", 20, False, "test-secret")


def test_web_api_key_persists_without_being_rendered(tmp_path):
    database_path = tmp_path / "app.sqlite3"
    app = create_app({"TESTING": True, "SETTINGS": settings(database_path)})
    browser = app.test_client()
    key = "private-test-key-123"

    response = browser.post("/admin/api-key", data={"action": "save", "api_key": key})
    assert response.status_code == 302
    assert app.extensions["wmata"].api_key == key
    assert app.extensions["database"].get_secret("wmata_api_key") == key
    assert key.encode() not in browser.get("/admin").data
    assert key not in str(app.extensions["database"].meta())

    restarted = create_app({"TESTING": True, "SETTINGS": settings(database_path)})
    assert restarted.extensions["wmata"].api_key == key


def test_environment_key_wins_after_restart_and_saved_key_can_be_removed(tmp_path):
    database_path = tmp_path / "app.sqlite3"
    app = create_app({"TESTING": True, "SETTINGS": settings(database_path, "environment-key")})
    browser = app.test_client()
    browser.post("/admin/api-key", data={"action": "save", "api_key": "web-key"})
    assert app.extensions["wmata"].api_key == "web-key"

    restarted = create_app({"TESTING": True, "SETTINGS": settings(database_path, "environment-key")})
    assert restarted.extensions["wmata"].api_key == "environment-key"
    response = restarted.test_client().post("/admin/api-key", data={"action": "remove"})
    assert response.status_code == 302
    assert restarted.extensions["database"].get_secret("wmata_api_key") == ""
    assert restarted.extensions["wmata"].api_key == "environment-key"


def test_invalid_api_key_is_rejected(tmp_path):
    app = create_app({"TESTING": True, "SETTINGS": settings(tmp_path / "app.sqlite3")})
    response = app.test_client().post("/admin/api-key", data={"api_key": "has whitespace"})
    assert response.status_code == 400
    assert app.extensions["database"].get_secret("wmata_api_key") == ""

