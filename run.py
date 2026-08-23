from app import create_app

app = create_app()

if __name__ == "__main__":
    settings = app.config["SETTINGS"]
    app.run(host=settings.bind, port=settings.port, threaded=True)

