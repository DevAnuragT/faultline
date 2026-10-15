from flask import Flask, jsonify, request

from . import greeting, preview, settings_loader

app = Flask(__name__)


@app.route("/settings/import", methods=["POST"])
def import_settings():
    return jsonify(settings_loader.load_user_settings(request.get_data()))


@app.route("/preview")
def link_preview():
    return preview.fetch_preview(request.args["url"])


@app.route("/hello")
def hello():
    return greeting.render_greeting(request.args.get("name", "world"))


@app.route("/health")
def health():
    return "ok"
