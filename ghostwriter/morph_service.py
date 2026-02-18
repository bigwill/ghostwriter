"""Standalone morph service -- keeps the word-embedding model warm.

Run once per dev session, then point the web app at it:

    Terminal 1:  ghostwriter-morph
    Terminal 2:  MORPH_SERVICE_URL=http://localhost:5001 ghostwriter-web

In production (Fly.io) this service is NOT used; the web app loads
the model in-process.
"""

from __future__ import annotations

import logging
import os
import socket
import threading

from flask import Flask, jsonify, request

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("morph-service")

API_KEY = os.environ.get("GHOSTWRITER_API_KEY", "")

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Background model loader (same pattern as server.py)
# ---------------------------------------------------------------------------

_model_ready = False
_preload_error: str | None = None


def _preload() -> None:
    global _model_ready, _preload_error
    try:
        log.info("PRELOAD: starting gensim import...")
        from ghostwriter.morph import load_model

        log.info("PRELOAD: gensim imported, loading model...")
        load_model()
        _model_ready = True
        log.info("PRELOAD: model ready!")
    except Exception as e:
        _preload_error = str(e)
        log.exception("PRELOAD ERROR: %s", e)


threading.Thread(target=_preload, daemon=True).start()

# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.route("/health")
def health():
    info: dict = {"ready": _model_ready}
    if _preload_error:
        info["error"] = _preload_error
    return jsonify(info)


@app.route("/morph", methods=["POST"])
def morph():
    # Optional API key check (mirrors the web app)
    if API_KEY:
        provided = request.headers.get("X-Api-Key") or request.args.get("key")
        if provided != API_KEY:
            return jsonify({"error": "Forbidden"}), 403

    if not _model_ready:
        return jsonify({"error": "Model still loading..."}), 503

    data = request.get_json()
    vibe = data.get("vibe", "")
    words = data.get("words", [])

    from ghostwriter.morph import morph_word, morph_emoji, is_emoji

    results = []
    for item in words:
        if item.get("isEmoji") or is_emoji(item["word"]):
            mr = morph_emoji(item["word"], vibe)
        else:
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


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    port = int(os.environ.get("MORPH_PORT", 5001))
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
    except Exception:
        ip = "localhost"
    print(f"morph service  ->  http://localhost:{port}")
    print(f"               ->  http://{ip}:{port}")
    print()
    print("Loading embeddings in background (first run downloads ~128 MB)...")
    app.run(host="0.0.0.0", port=port, debug=False)


if __name__ == "__main__":
    main()
