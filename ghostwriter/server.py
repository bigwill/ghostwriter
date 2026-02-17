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
import logging
import os
import re
import socket
import threading
import time
from pathlib import Path

from flask import Flask, abort, jsonify, render_template, request

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("ghostwriter")

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


@app.route("/og/<poem_id>.png")
def og_image(poem_id: str):
    """Generate a 1200x630 OG image (PNG) for iMessage / social previews."""
    import html as _html_mod
    import io

    if not re.fullmatch(r"[a-f0-9]{8}", poem_id):
        return "Not found", 404
    path = POEMS_DIR / f"{poem_id}.html"
    if not path.exists():
        return "Not found", 404

    content = path.read_text(encoding="utf-8")
    title_m = re.search(r"<title>(.*?)</title>", content)
    title = _html_mod.unescape(title_m.group(1)) if title_m else "A Poem"

    desc_m = re.search(r'og:description" content="(.*?)"', content)
    desc = _html_mod.unescape(desc_m.group(1)) if desc_m else ""

    # Split description into lines
    if " \u2022 " in desc:
        lines = desc.split(" \u2022 ")
    elif " / " in desc:
        lines = desc.split(" / ")
    else:
        lines = [desc] if desc else []

    try:
        from PIL import Image, ImageDraw, ImageFont

        W, H = 1200, 630
        # Light mode — matching the website's default palette
        BG = (250, 248, 245)      # #faf8f5
        ACCENT = (122, 78, 45)    # #7a4e2d
        FG = (44, 44, 44)         # #2c2c2c
        MUTED = (176, 160, 144)   # #b0a090

        img = Image.new("RGBA", (W, H), BG + (255,))
        draw = ImageDraw.Draw(img)

        # Load text font (serif) and emoji font
        def _load(paths: list[str], size: int):
            for p in paths:
                try:
                    return ImageFont.truetype(p, size)
                except (OSError, IOError):
                    continue
            return ImageFont.load_default()

        SERIF_PATHS = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/System/Library/Fonts/Georgia.ttf",
        ]
        EMOJI_PATHS = [
            "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",
            "/System/Library/Fonts/Apple Color Emoji.ttc",
        ]

        font_brand = _load(SERIF_PATHS, 22)
        font_title = _load(SERIF_PATHS, 44)
        font_body = _load(SERIF_PATHS, 30)

        # NotoColorEmoji is CBDT bitmap — only works at native size 109
        font_emoji = None
        for p in EMOJI_PATHS:
            try:
                font_emoji = ImageFont.truetype(p, 109)
                break
            except (OSError, IOError):
                continue

        def _is_emoji(c: str) -> bool:
            cp = ord(c)
            return (
                0x1F600 <= cp <= 0x1F64F
                or 0x1F300 <= cp <= 0x1F5FF
                or 0x1F680 <= cp <= 0x1F6FF
                or 0x1F900 <= cp <= 0x1F9FF
                or 0x1FA00 <= cp <= 0x1FAFF
                or 0x2600 <= cp <= 0x26FF
                or 0x2700 <= cp <= 0x27BF
                or 0x2300 <= cp <= 0x23FF
                or 0x2B50 <= cp <= 0x2B55
                or (0x203C <= cp <= 0x3299
                    and cp not in range(0x2100, 0x2200))
            )

        def _render_emoji(ch: str, target_h: int) -> Image.Image | None:
            """Render a single emoji at native size, scale to target_h."""
            if not font_emoji:
                return None
            try:
                bbox = font_emoji.getbbox(ch)
                if not bbox or bbox[2] - bbox[0] == 0:
                    return None
                ew, eh = bbox[2] - bbox[0], bbox[3] - bbox[1]
                tmp = Image.new("RGBA", (ew + 20, eh + 20), (0, 0, 0, 0))
                d = ImageDraw.Draw(tmp)
                d.text((-bbox[0], -bbox[1]), ch, font=font_emoji,
                       embedded_color=True)
                # Scale down to target height
                ratio = target_h / eh
                new_w = max(1, int(ew * ratio))
                return tmp.resize((new_w, target_h), Image.LANCZOS)
            except Exception:
                return None

        def _draw_line(
            x: int, y: int, text: str, fill: tuple,
            text_font, emoji_h: int,
        ) -> None:
            """Draw text with automatic emoji rendering + scaling."""
            for ch in text:
                if _is_emoji(ch):
                    emoji_img = _render_emoji(ch, emoji_h)
                    if emoji_img:
                        img.paste(emoji_img, (x, y), emoji_img)
                        x += emoji_img.width + 2
                    else:
                        x += 4  # skip unknown emoji
                elif ord(ch) >= 0xFE00:
                    continue  # skip variation selectors
                else:
                    draw.text((x, y), ch, fill=fill, font=text_font)
                    bbox = text_font.getbbox(ch)
                    x += (bbox[2] - bbox[0]) if bbox else 12

        draw.text((100, 80), "ghostwriter", fill=MUTED + (255,), font=font_brand)
        _draw_line(100, 150, title[:50], ACCENT + (255,), font_title, 44)

        y = 260
        for ln in lines[:6]:
            _draw_line(100, y, ln[:65], FG + (255,), font_body, 30)
            y += 48

        # Flatten RGBA → RGB (emoji compositing needs alpha channel)
        flat = Image.new("RGB", img.size, BG)
        flat.paste(img, mask=img.split()[3])

        buf = io.BytesIO()
        flat.save(buf, format="PNG", optimize=True)
        buf.seek(0)

        from flask import Response

        return Response(buf.getvalue(), mimetype="image/png", headers={
            "Cache-Control": "public, max-age=86400",
        })

    except ImportError:
        from flask import Response

        return Response("Pillow not installed", status=500)


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


@app.route("/health")
def health():
    info: dict = {"status": "ok", "model_ready": _model_ready}
    if _preload_error:
        info["error"] = _preload_error
    return jsonify(info)


@app.route("/api/status")
def api_status():
    return jsonify({"model_ready": _model_ready})


@app.route("/api/debug")
def api_debug():
    """Temporary debug endpoint to inspect volume & env."""
    import shutil

    gensim_dir = os.environ.get("GENSIM_DATA_DIR", "~/gensim-data")
    data_dir = Path("/data")
    gensim_path = Path(gensim_dir)

    def tree(p: Path, depth: int = 2) -> list:
        items = []
        if not p.exists():
            return [f"(not found: {p})"]
        try:
            for child in sorted(p.iterdir()):
                info = f"{child.name}/"
                if child.is_file():
                    info = f"{child.name} ({child.stat().st_size:,} bytes)"
                items.append(info)
                if child.is_dir() and depth > 0:
                    for sub in tree(child, depth - 1):
                        items.append(f"  {sub}")
        except PermissionError:
            items.append("(permission denied)")
        return items

    disk = shutil.disk_usage("/data") if data_dir.exists() else None
    return jsonify(
        {
            "gensim_data_dir_env": gensim_dir,
            "gensim_path_exists": gensim_path.exists(),
            "gensim_contents": tree(gensim_path),
            "data_contents": tree(data_dir),
            "disk": {
                "total_mb": disk.total // (1024 * 1024),
                "used_mb": disk.used // (1024 * 1024),
                "free_mb": disk.free // (1024 * 1024),
            }
            if disk
            else None,
            "model_ready": _model_ready,
            "preload_error": _preload_error,
            "preload_thread_alive": any(
                t.name == "Thread-1" or "_preload" in str(t)
                for t in threading.enumerate()
            ),
            "active_threads": [t.name for t in threading.enumerate()],
        }
    )


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

    # Always use https for OG URLs (Fly.io proxies as http internally)
    scheme = "https" if request.headers.get("X-Forwarded-Proto") == "https" or request.host != "localhost" else "http"
    base_url = f"{scheme}://{request.host}/p/{poem_id}"

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
