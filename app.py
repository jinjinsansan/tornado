"""TornadoAI — WIN5特化予想サービス Flask App."""

import logging
import sys

from flask import Flask
from flask_cors import CORS

from api.web_chat import bp as web_chat_bp
from api.auth import bp as auth_bp
from api.win5 import bp as win5_bp

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

flask_app = Flask(__name__)
flask_app.url_map.strict_slashes = False

# CORS (frontend domain — update after domain is decided)
CORS(flask_app, resources={
    r"/api/*": {
        "origins": [
            "http://localhost:3000",
            "https://www.tornadeai.com",
            "https://tornadeai.com",
        ],
        "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        "allow_headers": ["Content-Type", "Authorization"],
    }
})

flask_app.register_blueprint(web_chat_bp)
flask_app.register_blueprint(auth_bp)
flask_app.register_blueprint(win5_bp)


@flask_app.route("/health", methods=["GET"])
def health():
    return "OK"


# Gunicorn entry point
app = flask_app
