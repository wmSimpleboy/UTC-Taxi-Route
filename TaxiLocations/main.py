import os

from dotenv import load_dotenv

load_dotenv()


def main() -> None:
    # Run Flask web application (defined in ui_app.py).
    from ui_app import create_app

    app = create_app()
    port = int(os.environ.get("FLASK_PORT", "5002"))
    debug = os.environ.get("FLASK_DEBUG") == "1"
    app.run(debug=debug, port=port)


if __name__ == "__main__":
    main()
