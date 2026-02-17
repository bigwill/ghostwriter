"""Ghostwriter web -- a phone-friendly editor for morphing writings.

Run with:
    ghostwriter-web     (after pip install -e .)
    python -m ghostwriter.server

Environment variables:
    GHOSTWRITER_POEMS_DIR   Where to store writing HTML files (default: ./poems)
    GHOSTWRITER_API_KEY     Protect POST endpoints; leave unset for open access
    GHOSTWRITER_PASSWORD    Password for the editor / admin features
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

from flask import (
    Flask,
    abort,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("ghostwriter")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

POEMS_DIR = Path(os.environ.get("GHOSTWRITER_POEMS_DIR", "poems"))
API_KEY = os.environ.get("GHOSTWRITER_API_KEY", "")
PASSWORD = os.environ.get("GHOSTWRITER_PASSWORD", "")

app = Flask(__name__)
app.secret_key = os.environ.get(
    "FLASK_SECRET_KEY",
    hashlib.sha256((API_KEY or "ghostwriter-dev").encode()).hexdigest(),
)

# Ensure poems directory exists at startup
POEMS_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------


def _require_api_key() -> None:
    """Abort 403 if an API key is configured and the request lacks it."""
    if not API_KEY:
        return  # no key set → open access (local dev)
    provided = request.headers.get("X-Api-Key") or request.args.get("key")
    if provided != API_KEY:
        abort(403)


def _is_authenticated() -> bool:
    """Return True if the user has logged in via session."""
    if not PASSWORD:
        return True  # no password set → always authenticated (local dev)
    return session.get("authenticated") is True


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
    writings: list[dict] = []
    if POEMS_DIR.exists():
        for f in sorted(
            POEMS_DIR.glob("*.html"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        ):
            content = f.read_text(encoding="utf-8")
            title_m = re.search(r"<title>(.*?)</title>", content)
            desc_m = re.search(r'og:description" content="(.*?)"', content)
            writings.append(
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
    return render_template(
        "gallery.html",
        writings=writings,
        authenticated=_is_authenticated(),
    )


@app.route("/login", methods=["GET", "POST"])
def login():
    if _is_authenticated():
        return redirect(url_for("write"))
    error = None
    if request.method == "POST":
        if request.form.get("password") == PASSWORD:
            session["authenticated"] = True
            return redirect(request.args.get("next") or url_for("write"))
        error = "wrong password"
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.pop("authenticated", None)
    return redirect(url_for("index"))


@app.route("/poems")
def poems_redirect():
    return redirect(url_for("index"), code=301)


@app.route("/write")
def write():
    if not _is_authenticated():
        return redirect(url_for("login", next="/write"))
    return render_template("editor.html", api_key=API_KEY)


@app.route("/p/<poem_id>")
def view_poem(poem_id: str):
    if not re.fullmatch(r"[a-f0-9]{8}", poem_id):
        return "Not found", 404
    path = POEMS_DIR / f"{poem_id}.html"
    if not path.exists():
        return "Not found", 404
    return path.read_text(encoding="utf-8")


def _parse_og_content(poem_id: str):
    """Parse a saved writing's HTML and return (title, lines, cycling_data) or None."""
    import html as _html_mod
    import json as _json_mod

    if not re.fullmatch(r"[a-f0-9]{8}", poem_id):
        return None
    path = POEMS_DIR / f"{poem_id}.html"
    if not path.exists():
        return None

    content = path.read_text(encoding="utf-8")
    title_m = re.search(r"<title>(.*?)</title>", content)
    title = _html_mod.unescape(title_m.group(1)) if title_m else "Untitled"

    desc_m = re.search(r'og:description" content="(.*?)"', content)
    desc = _html_mod.unescape(desc_m.group(1)) if desc_m else ""

    if " \u2022 " in desc:
        lines = desc.split(" \u2022 ")
    elif " / " in desc:
        lines = desc.split(" / ")
    else:
        lines = [desc] if desc else []

    # Find cycling words: [(displayed_word, [alternatives], case_type)]
    cycling = []
    for m in re.finditer(
        r"data-original=\"([^\"]*)\"\s*"
        r"data-case=\"([^\"]*)\"\s*"
        r"data-words='(\[[^']*\])'"
        r"[^>]*>([^<]+)<",
        content,
    ):
        words = _json_mod.loads(_html_mod.unescape(m.group(3)))
        if len(words) > 1:
            cycling.append(
                {
                    "current": _html_mod.unescape(m.group(4)),
                    "words": words,
                    "case": m.group(2),
                }
            )

    return title, lines, cycling


def _load_og_fonts():
    """Load and return (font_brand, font_title, font_body, font_emoji)."""
    from PIL import ImageFont

    def _load(paths: list[str], size: int):
        for p in paths:
            try:
                return ImageFont.truetype(p, size)
            except (OSError, IOError):
                continue
        return ImageFont.load_default()

    serif = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/Georgia.ttf",
    ]
    emoji_paths = [
        "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",
        "/System/Library/Fonts/Apple Color Emoji.ttc",
    ]

    font_emoji = None
    for p in emoji_paths:
        try:
            font_emoji = ImageFont.truetype(p, 109)
            break
        except (OSError, IOError):
            continue

    return _load(serif, 22), _load(serif, 44), _load(serif, 30), font_emoji


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
        or (0x203C <= cp <= 0x3299 and cp not in range(0x2100, 0x2200))
    )


_OG_W, _OG_H = 1200, 630
_OG_BG = (250, 248, 245)
_OG_ACCENT = (122, 78, 45)
_OG_FG = (44, 44, 44)
_OG_MUTED = (176, 160, 144)


def _render_og_frame(title, lines, font_brand, font_title, font_body, font_emoji):
    """Return an RGB PIL Image for one OG frame."""
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (_OG_W, _OG_H), _OG_BG + (255,))
    draw = ImageDraw.Draw(img)

    def _render_emoji(ch, target_h):
        if not font_emoji:
            return None
        try:
            bbox = font_emoji.getbbox(ch)
            if not bbox or bbox[2] - bbox[0] == 0:
                return None
            ew, eh = bbox[2] - bbox[0], bbox[3] - bbox[1]
            tmp = Image.new("RGBA", (ew + 20, eh + 20), (0, 0, 0, 0))
            ImageDraw.Draw(tmp).text(
                (-bbox[0], -bbox[1]), ch, font=font_emoji, embedded_color=True
            )
            ratio = target_h / eh
            return tmp.resize((max(1, int(ew * ratio)), target_h), Image.LANCZOS)
        except Exception:
            return None

    def _draw_line(x, y, text, fill, text_font, emoji_h):
        for ch in text:
            if _is_emoji(ch):
                ei = _render_emoji(ch, emoji_h)
                if ei:
                    img.paste(ei, (x, y), ei)
                    x += ei.width + 2
                else:
                    x += 4
            elif ord(ch) >= 0xFE00:
                continue
            else:
                draw.text((x, y), ch, fill=fill, font=text_font)
                bbox = text_font.getbbox(ch)
                x += (bbox[2] - bbox[0]) if bbox else 12

    # Left-justified with generous margin to survive iMessage cropping
    LM = 250

    draw.text((LM, 100), "ghostwriter", fill=_OG_MUTED + (255,), font=font_brand)
    _draw_line(LM, 170, title[:50], _OG_ACCENT + (255,), font_title, 44)

    y = 270
    for ln in lines[:5]:
        _draw_line(LM, y, ln[:60], _OG_FG + (255,), font_body, 30)
        y += 46

    flat = Image.new("RGB", img.size, _OG_BG)
    flat.paste(img, mask=img.split()[3])
    return flat


def _apply_case(word: str, case_type: str) -> str:
    if case_type == "upper":
        return word.upper()
    if case_type == "title":
        return word[0].upper() + word[1:]
    return word


@app.route("/og/<poem_id>.png")
def og_image(poem_id: str):
    """Static OG image (backwards compatibility)."""
    import io

    parsed = _parse_og_content(poem_id)
    if not parsed:
        return "Not found", 404
    title, lines, _ = parsed

    try:
        fb, ft, fbo, fe = _load_og_fonts()
        img = _render_og_frame(title, lines, fb, ft, fbo, fe)
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        buf.seek(0)
        from flask import Response

        return Response(
            buf.getvalue(),
            mimetype="image/png",
            headers={"Cache-Control": "public, max-age=86400"},
        )
    except ImportError:
        from flask import Response

        return Response("Pillow not installed", status=500)


@app.route("/og/<poem_id>.gif")
def og_image_gif(poem_id: str):
    """Animated OG image — cycles through alternative words."""
    import io

    from flask import Response

    # Serve from cache if available
    cache_dir = POEMS_DIR / "og_cache"
    cache_path = cache_dir / f"{poem_id}.gif"
    if cache_path.exists():
        return Response(
            cache_path.read_bytes(),
            mimetype="image/gif",
            headers={"Cache-Control": "public, max-age=86400"},
        )

    parsed = _parse_og_content(poem_id)
    if not parsed:
        return "Not found", 404
    title, lines, cycling = parsed

    if not cycling:
        return redirect(f"/og/{poem_id}.png")

    try:
        from PIL import Image, ImageDraw, ImageFont

        W, H = 1200, 630
        fb, ft, fbo, _ = _load_og_fonts()

        max_words = max(len(c["words"]) for c in cycling)
        num_frames = min(max_words, 4)

        LM = 250

        def _render_simple(frame_title, frame_lines):
            """Fast text-only frame (no emoji) for GIF."""
            img = Image.new("RGB", (W, H), _OG_BG)
            draw = ImageDraw.Draw(img)
            draw.text((LM, 100), "ghostwriter", fill=_OG_MUTED, font=fb)
            draw.text((LM, 170), frame_title[:50], fill=_OG_ACCENT, font=ft)
            y = 270
            for ln in frame_lines[:5]:
                draw.text((LM, y), ln[:60], fill=_OG_FG, font=fbo)
                y += 46
            return img

        frames = []
        for fi in range(num_frames):
            frame_title = title
            frame_lines = list(lines)
            for c in cycling:
                word = c["words"][fi % len(c["words"])]
                cased = _apply_case(word, c["case"])
                frame_title = frame_title.replace(c["current"], cased)
                frame_lines = [ln.replace(c["current"], cased) for ln in frame_lines]
            frames.append(_render_simple(frame_title, frame_lines))

        # Use first frame's palette for all frames (fast, consistent)
        palette_img = frames[0].quantize(colors=128, method=2)
        palette = palette_img.getpalette()
        gif_frames = []
        for f in frames:
            q = f.quantize(colors=128, method=2)
            q.putpalette(palette)
            gif_frames.append(q)

        buf = io.BytesIO()
        gif_frames[0].save(
            buf,
            format="GIF",
            save_all=True,
            append_images=gif_frames[1:],
            duration=2500,
            loop=0,
        )
        buf.seek(0)
        data = buf.getvalue()

        # Cache to disk
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(data)

        return Response(
            data,
            mimetype="image/gif",
            headers={"Cache-Control": "public, max-age=86400"},
        )
    except ImportError:
        return redirect(f"/og/{poem_id}.png")


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


@app.route("/api/delete/<writing_id>", methods=["DELETE"])
def api_delete(writing_id: str):
    if not _is_authenticated():
        abort(403)
    if not re.fullmatch(r"[a-f0-9]{8}", writing_id):
        return jsonify({"error": "Invalid id"}), 400
    path = POEMS_DIR / f"{writing_id}.html"
    if not path.exists():
        return jsonify({"error": "Not found"}), 404
    path.unlink()
    return jsonify({"ok": True})


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
