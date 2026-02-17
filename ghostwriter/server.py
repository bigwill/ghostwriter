"""Ghostwriter web -- a phone-friendly editor for morphing poems.

Run with:
    ghostwriter-web     (after pip install -e .)
    python -m ghostwriter.server

Environment variables:
    GHOSTWRITER_POEMS_DIR   Where to store poem HTML files (default: ./poems)
    GHOSTWRITER_API_KEY     Protect POST endpoints; leave unset for open access
    GENSIM_DATA_DIR         Cache location for GloVe vectors (default: ~/gensim-data)
"""

from __future__ import annotations

import hashlib
import os
import re
import socket
import threading
import time
from pathlib import Path

from flask import Flask, abort, jsonify, render_template, request

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

POEMS_DIR = Path(os.environ.get("GHOSTWRITER_POEMS_DIR", "poems"))
API_KEY = os.environ.get("GHOSTWRITER_API_KEY", "")

app = Flask(__name__)

# Ensure poems directory exists at startup
POEMS_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Auth helper
# ---------------------------------------------------------------------------


def _require_api_key() -> None:
    """Abort 403 if an API key is configured and the request lacks it."""
    if not API_KEY:
        return  # no key set → open access (local dev)
    provided = request.headers.get("X-Api-Key") or request.args.get("key")
    if provided != API_KEY:
        abort(403)


# ---------------------------------------------------------------------------
# Background model loader
# ---------------------------------------------------------------------------

_model_ready = False


def _preload() -> None:
    global _model_ready
    from ghostwriter.morph import load_model

    load_model()
    _model_ready = True


threading.Thread(target=_preload, daemon=True).start()

# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


@app.route("/")
def index():
    # Inject the API key so browser JS can authenticate to POST endpoints
    return render_template("editor.html", api_key=API_KEY)


@app.route("/poems")
def gallery():
    poems: list[dict] = []
    if POEMS_DIR.exists():
        for f in sorted(
            POEMS_DIR.glob("*.html"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        ):
            content = f.read_text(encoding="utf-8")
            title_m = re.search(r"<title>(.*?)</title>", content)
            desc_m = re.search(r'og:description" content="(.*?)"', content)
            poems.append(
                {
                    "id": f.stem,
                    "title": title_m.group(1) if title_m else f.stem,
                    "desc": desc_m.group(1) if desc_m else "",
                    "date": time.strftime(
                        "%b %d, %Y at %I:%M %p",
                        time.localtime(f.stat().st_mtime),
                    ),
                }
            )
    return render_template("gallery.html", poems=poems)


@app.route("/p/<poem_id>")
def view_poem(poem_id: str):
    if not re.fullmatch(r"[a-f0-9]{8}", poem_id):
        return "Not found", 404
    path = POEMS_DIR / f"{poem_id}.html"
    if not path.exists():
        return "Not found", 404
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


@app.route("/health")
def health():
    return jsonify({"status": "ok", "model_ready": _model_ready})


@app.route("/api/status")
def api_status():
    return jsonify({"model_ready": _model_ready})


@app.route("/api/morph", methods=["POST"])
def api_morph():
    _require_api_key()

    if not _model_ready:
        return jsonify({"error": "Model still loading..."}), 503

    data = request.get_json()
    vibe = data.get("vibe", "")
    words = data.get("words", [])

    from ghostwriter.morph import morph_word

    results = []
    for item in words:
        mr = morph_word(
            item["word"],
            vibe,
            context=item.get("context") or None,
        )
        results.append(
            {
                "original": mr.original,
                "candidates": [
                    {"word": c.word, "score": c.score} for c in mr.candidates
                ],
            }
        )

    return jsonify({"results": results})


@app.route("/api/save", methods=["POST"])
def api_save():
    _require_api_key()

    data = request.get_json()
    text = data.get("text", "")
    morphed = data.get("morphed", {})

    POEMS_DIR.mkdir(parents=True, exist_ok=True)
    poem_id = hashlib.sha256(
        f"{time.time():.6f}{text[:100]}".encode()
    ).hexdigest()[:8]

    base_url = f"{request.scheme}://{request.host}/p/{poem_id}"

    from ghostwriter.web import render_poem_html

    html = render_poem_html(text, morphed=morphed, base_url=base_url)
    (POEMS_DIR / f"{poem_id}.html").write_text(html, encoding="utf-8")

    return jsonify({"id": poem_id, "url": f"/p/{poem_id}", "full_url": base_url})


# ---------------------------------------------------------------------------
# Entry point (local dev)
# ---------------------------------------------------------------------------


def _local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "localhost"


def main() -> None:
    ip = _local_ip()
    port = int(os.environ.get("PORT", 5000))
    print(f"ghostwriter web  ->  http://localhost:{port}")
    print(f"from your phone  ->  http://{ip}:{port}")
    if API_KEY:
        print(f"API key          ->  {API_KEY[:4]}...{API_KEY[-4:]}")
    else:
        print("API key          ->  (none, open access)")
    print()
    print("Loading embeddings in background (first run downloads ~128 MB)...")
    app.run(host="0.0.0.0", port=port, debug=False)


if __name__ == "__main__":
    main()
