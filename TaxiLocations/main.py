import os

from dotenv import load_dotenv

load_dotenv()


def main() -> None:
    # Run Flask web application (defined in ui_app.py).
    from ui_app import create_app

    app = create_app()
    port = int(os.environ.get("FLASK_PORT", "5002"))
    debug = os.environ.get("FLASK_DEBUG") == "1"
    # Use FLASK_HOST=0.0.0.0 in Docker so the port is reachable from the host.
    host = os.environ.get("FLASK_HOST", "127.0.0.1")
    app.run(debug=debug, host=host, port=port)


if __name__ == "__main__":
    main()
