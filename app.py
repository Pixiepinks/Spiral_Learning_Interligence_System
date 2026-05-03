import os

from flask import Flask

app = Flask(__name__)


@app.route("/")
def home() -> str:
    return "Spiral Learning System Running"


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
