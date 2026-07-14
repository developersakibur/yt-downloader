# app/app.py
"""
Application entrypoint.

Starts the database, recovers any stale locks from a previous run,
starts the worker pool, and serves the Web API + static Web UI.
"""

import os
import sys

# Make sure all sub-modules (api, database, downloader, scanner, cookies)
# are importable regardless of where Python is launched from.
_APP_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _APP_DIR)
sys.path.insert(0, os.path.join(_APP_DIR, "database"))
sys.path.insert(0, os.path.join(_APP_DIR, "downloader"))
sys.path.insert(0, os.path.join(_APP_DIR, "cookies"))
sys.path.insert(0, os.path.join(_APP_DIR, "scanner"))

from flask import Flask, render_template, send_from_directory

import database as db
import worker
import scanner

from api.routes import api

WEB_DIR = os.path.join(os.path.dirname(_APP_DIR), "web")

app = Flask(__name__, static_folder=WEB_DIR, template_folder=WEB_DIR, static_url_path="")
app.register_blueprint(api)


@app.after_request
def add_cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PATCH, DELETE, PUT, OPTIONS"
    return resp


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/thumbnails/<video_id>.jpg")
def serve_thumbnail(video_id):
    """Serve cached thumbnails. UI uses this — never re-requests YouTube."""
    cache_dir = scanner.get_thumbnail_dir()
    return send_from_directory(cache_dir, f"{video_id}.jpg")


_pool = worker.WorkerPool()


def resize_worker_pool(new_count):
    """Called by the Settings API when concurrent_downloads changes,
    so a new limit takes effect immediately instead of needing a restart."""
    return _pool.resize(new_count)


def start_app():
    db.init_db()
    started = _pool.start()
    worker.start_retry_sweeper()
    from logging_setup import get_logger
    get_logger("app").info(f"Database ready. {started} download worker(s) started.")
    print(f"✅ Database ready. {started} download worker(s) started.")


if __name__ == "__main__":
    start_app()
    app.run(debug=False, host="127.0.0.1", port=5000, threaded=True)
