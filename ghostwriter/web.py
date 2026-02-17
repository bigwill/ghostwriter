"""Web view for sharing poems.

Generates a beautiful, self-contained HTML page with Open Graph meta
tags for rich previews in iOS Messages / social media.  Includes a
lightweight stdlib HTTP server for local preview.
"""

from __future__ import annotations

import html as _esc
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Optional

_WORD_RE = re.compile(r"[A-Za-z]+")

# ---------------------------------------------------------------------------
# HTML template
# ---------------------------------------------------------------------------

_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>

<!-- Open Graph — powers iOS Messages / social link previews -->
<meta property="og:type" content="article">
<meta property="og:title" content="{og_title}">
<meta property="og:description" content="{og_description}">
<meta property="og:site_name" content="Ghostwriter">
{og_url_tag}
<meta name="twitter:card" content="summary">
<meta name="twitter:title" content="{og_title}">
<meta name="twitter:description" content="{og_description}">

<link rel="icon" href="data:image/svg+xml,\
<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'>\
<text y='.9em' font-size='90'>&#x1f47b;</text></svg>">

<style>
*,*::before,*::after {{ margin:0; padding:0; box-sizing:border-box; }}

:root {{
  --bg:    #faf8f5;
  --fg:    #2c2c2c;
  --muted: #b0a090;
  --ghost: rgba(0,0,0,0.12);
  --accent:#7a4e2d;
}}
@media (prefers-color-scheme: dark) {{
  :root {{
    --bg:    #1a1916;
    --fg:   #e0dbd4;
    --muted:#6e6358;
    --ghost:rgba(255,255,255,0.12);
    --accent:#c9a57a;
  }}
}}

html {{ height:100%; }}
body {{
  font-family: Georgia, 'Times New Roman', 'Noto Serif', serif;
  background: var(--bg);
  color: var(--fg);
  min-height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 4rem 1.5rem 3rem;
  -webkit-font-smoothing: antialiased;
}}

.poem {{
  max-width: 34em;
  width: 100%;
  font-size: 1.15rem;
  line-height: 2;
  letter-spacing: 0.01em;
}}
.poem p {{
  margin-bottom: 0.15em;
  text-indent: 0;
}}
.poem p.blank {{
  height: 1.6em;
}}

/* morphed words — subtle accent, ghost on hover */
.morphed {{
  color: var(--accent);
  position: relative;
  cursor: default;
  transition: color 0.2s;
  border-bottom: 1px dotted var(--ghost);
}}
.morphed::after {{
  content: attr(data-original);
  position: absolute;
  left: 50%;
  transform: translateX(-50%);
  bottom: 100%;
  font-size: 0.65em;
  color: var(--ghost);
  white-space: nowrap;
  pointer-events: none;
  opacity: 0;
  transition: opacity 0.35s ease;
}}
.morphed:hover::after,
.morphed.reveal::after {{
  opacity: 1;
}}

.brand {{
  font-variant: small-caps;
  letter-spacing: 0.18em;
  color: var(--muted);
  font-size: 0.7rem;
  margin-top: 3.5rem;
  text-align: center;
}}
.brand a {{
  color: inherit;
  text-decoration: none;
}}
.brand a:hover {{
  text-decoration: underline;
}}

@media (max-width: 480px) {{
  body {{ padding: 2.5rem 1.25rem 2rem; }}
  .poem {{ font-size: 1.05rem; line-height: 1.85; }}
}}
</style>
</head>
<body>

<article class="poem">
{poem_html}
</article>

<p class="brand"><a href="https://github.com/bigwill/ghostwriter">ghostwriter</a></p>

<script>
// Tap-to-reveal ghost originals on mobile
document.querySelectorAll('.morphed').forEach(function(el) {{
  el.addEventListener('click', function() {{
    el.classList.toggle('reveal');
  }});
}});
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# HTML generation
# ---------------------------------------------------------------------------


def _poem_to_html(text: str, morphed: dict[str, str]) -> str:
    """Convert poem text to HTML with morphed words wrapped in spans."""
    # Build reverse lookup: replacement_lower -> original_lower
    repl_to_orig: dict[str, str] = {}
    for orig, repl in morphed.items():
        repl_to_orig[repl.lower()] = orig.lower()

    lines_html: list[str] = []
    for line in text.splitlines():
        if not line.strip():
            lines_html.append('<p class="blank">&nbsp;</p>')
            continue
        parts: list[str] = []
        last = 0
        for m in _WORD_RE.finditer(line):
            # Non-word text before this match
            if m.start() > last:
                parts.append(_esc.escape(line[last : m.start()]))
            word = m.group()
            original = repl_to_orig.get(word.lower())
            if original:
                parts.append(
                    f'<span class="morphed" data-original='
                    f'"{_esc.escape(original)}">'
                    f"{_esc.escape(word)}</span>"
                )
            else:
                parts.append(_esc.escape(word))
            last = m.end()
        if last < len(line):
            parts.append(_esc.escape(line[last:]))
        lines_html.append(f'<p>{"".join(parts)}</p>')
    return "\n".join(lines_html)


def render_poem_html(
    text: str,
    morphed: dict[str, str] | None = None,
    title: str | None = None,
    base_url: str | None = None,
) -> str:
    """Return a complete, self-contained HTML page for the poem.

    Parameters
    ----------
    text:
        The final poem text (after morphing).
    morphed:
        ``{original_word: replacement_word}`` mapping.  Replacement words
        are highlighted in the output and show the original on hover.
    title:
        Page ``<title>`` and OG title.  Defaults to the first line.
    base_url:
        Public URL of the hosted page (for ``og:url``).
    """
    if morphed is None:
        morphed = {}

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not title:
        title = lines[0][:80] if lines else "A Poem"
    og_desc = " / ".join(lines[:3])[:200] if lines else ""

    og_url_tag = ""
    if base_url:
        og_url_tag = f'<meta property="og:url" content="{_esc.escape(base_url)}">'

    poem_html = _poem_to_html(text, morphed)

    return _TEMPLATE.format(
        title=_esc.escape(title),
        og_title=_esc.escape(title),
        og_description=_esc.escape(og_desc),
        og_url_tag=og_url_tag,
        poem_html=poem_html,
    )


def save_html(html_content: str, path: str | Path = "poem.html") -> Path:
    """Write the HTML to disk and return the resolved path."""
    p = Path(path)
    p.write_text(html_content, encoding="utf-8")
    return p.resolve()


# ---------------------------------------------------------------------------
# Local HTTP server (stdlib, zero dependencies)
# ---------------------------------------------------------------------------

_server: Optional[HTTPServer] = None
_server_thread: Optional[threading.Thread] = None
_current_html: str = ""


class _PoemHandler(BaseHTTPRequestHandler):
    """Serves the current poem HTML at every path."""

    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(_current_html.encode("utf-8"))

    def log_message(self, format: str, *args: object) -> None:
        pass  # suppress console noise


def start_server(html_content: str, port: int = 8000) -> str:
    """Start (or update) the local poem server.  Returns the URL."""
    global _server, _server_thread, _current_html
    _current_html = html_content

    if _server is not None:
        # Server already running — just swap content
        return f"http://localhost:{_server.server_address[1]}/"

    # Find an open port
    for p in range(port, port + 100):
        try:
            _server = HTTPServer(("", p), _PoemHandler)
            break
        except OSError:
            continue
    else:
        raise RuntimeError("Could not find an available port")

    _server_thread = threading.Thread(
        target=_server.serve_forever, daemon=True
    )
    _server_thread.start()
    return f"http://localhost:{_server.server_address[1]}/"


def update_content(html_content: str) -> None:
    """Swap the served HTML without restarting the server."""
    global _current_html
    _current_html = html_content
