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

_WORD_RE = re.compile(r"[A-Za-z]+|[\U0001F300-\U0001FAFF\u2600-\u27BF\u2300-\u23FF]")

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
<meta property="og:site_name" content="ghostwriter">
{og_url_tag}
{og_image_tag}
<meta name="description" content="{og_description}">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{og_title}">
<meta name="twitter:description" content="{og_description}">
{twitter_image_tag}

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
  border-bottom: 1px dotted var(--ghost);
  transition: opacity 0.35s ease;
}}
.morphed.fading {{
  opacity: 0;
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

.poem-title {{
  max-width: 34em;
  width: 100%;
  font-size: 1.4rem;
  font-weight: normal;
  font-style: italic;
  color: var(--accent);
  margin-bottom: 1.5rem;
  line-height: 1.4;
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

{title_html}
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

// Cycle through alternative words every 3 seconds (random per word)
(function() {{
  var cyclers = document.querySelectorAll('.morphed.cycling');
  if (!cyclers.length) return;
  cyclers.forEach(function(el) {{ el._ci = 0; }});
  function applyCase(word, caseType) {{
    if (caseType === 'upper') return word.toUpperCase();
    if (caseType === 'title') return word[0].toUpperCase() + word.slice(1);
    return word;
  }}
  function adjustArticle(el, rawWord) {{
    var prev = el.previousSibling;
    if (!prev || prev.nodeType !== 3) return;
    var t = prev.textContent;
    var vowel = 'aeiou'.indexOf(rawWord[0].toLowerCase()) >= 0;
    var fixed = t.replace(/(^|\s)(a|an|A|An|AN)\s$/, function(m, pre, art) {{
      var target = vowel ? 'an' : 'a';
      if (art === art.toUpperCase() && art.length > 1) target = target.toUpperCase();
      else if (art[0] === art[0].toUpperCase()) target = target[0].toUpperCase() + target.slice(1);
      return pre + target + ' ';
    }});
    if (fixed !== t) prev.textContent = fixed;
  }}
  setInterval(function() {{
    cyclers.forEach(function(el) {{
      var words;
      try {{ words = JSON.parse(el.getAttribute('data-words')); }}
      catch(e) {{ return; }}
      if (!words || words.length <= 1) return;
      var caseType = el.getAttribute('data-case') || 'lower';
      var cur = el._ci || 0;
      var next = Math.floor(Math.random() * (words.length - 1));
      if (next >= cur) next++;
      el._ci = next;
      var w = applyCase(words[next], caseType);
      el.classList.add('fading');
      setTimeout(function() {{
        el.textContent = w;
        adjustArticle(el, words[next]);
        el.classList.remove('fading');
      }}, 350);
    }});
  }}, 3000);
}})();
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# HTML generation
# ---------------------------------------------------------------------------


def _poem_to_html(text: str, morphed: dict[str, str | list[str]]) -> str:
    """Convert poem text to HTML with morphed words wrapped in spans.

    *morphed* values can be a single replacement string or a list of
    cycling words.  When a list is provided the first word is shown
    initially and the rest cycle via client-side JS.
    """
    import json as _json

    # Normalise: ensure every value is a list
    norm: dict[str, list[str]] = {}
    for orig, repl in morphed.items():
        if isinstance(repl, str):
            norm[orig.lower()] = [repl]
        else:
            norm[orig.lower()] = list(repl)

    # Build reverse lookup: first_replacement_lower -> (original_lower, all_words)
    repl_to_orig: dict[str, tuple[str, list[str]]] = {}
    for orig, words in norm.items():
        if words:
            repl_to_orig[words[0].lower()] = (orig, words)

    lines_html: list[str] = []
    for line in text.splitlines():
        if not line.strip():
            lines_html.append('<p class="blank">&nbsp;</p>')
            continue
        parts: list[str] = []
        last = 0
        for m in _WORD_RE.finditer(line):
            if m.start() > last:
                parts.append(_esc.escape(line[last : m.start()]))
            word = m.group()
            entry = repl_to_orig.get(word.lower())
            if entry:
                original, all_words = entry
                words_json = _esc.escape(_json.dumps(all_words))
                cls = "morphed cycling" if len(all_words) > 1 else "morphed"
                # Detect case pattern from the word as it appears in the poem
                if not word[0].isalpha():
                    case = "lower"  # emoji or symbol — no case
                elif word == word.upper() and len(word) > 1:
                    case = "upper"
                elif word[0].isupper():
                    case = "title"
                else:
                    case = "lower"
                parts.append(
                    f'<span class="{cls}"'
                    f' data-original="{_esc.escape(original)}"'
                    f' data-case="{case}"'
                    f" data-words='{words_json}'>"
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
    morphed: dict[str, str | list[str]] | None = None,
    title: str | None = None,
    base_url: str | None = None,
) -> str:
    """Return a complete, self-contained HTML page for the poem.

    Parameters
    ----------
    text:
        The final poem text (after morphing).
    morphed:
        ``{original_word: replacement_word}`` or
        ``{original_word: [word1, word2, ...]}`` mapping.  When a list
        is provided, the shared page cycles through them every 3 s.
    title:
        Page ``<title>`` and OG title.  Defaults to the first line.
    base_url:
        Public URL of the hosted page (for ``og:url``).
    """
    if morphed is None:
        morphed = {}

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    has_explicit_title = bool(title)
    if not title:
        title = lines[0][:80] if lines else "Untitled"
    # Use newline-separated lines for a readable preview
    og_desc = " \u2022 ".join(lines[:4])[:200] if lines else ""

    # Always use https for OG URL
    og_url_tag = ""
    if base_url:
        secure_url = base_url.replace("http://", "https://", 1)
        og_url_tag = (
            f'<meta property="og:url" content="{_esc.escape(secure_url)}">'
        )

    # OG image — use the poem-specific OG image endpoint if we have a URL
    og_image_tag = ""
    twitter_image_tag = ""
    if base_url:
        # Extract poem_id from the URL path /p/<id>
        _id_match = re.search(r"/p/([a-f0-9]+)", base_url)
        if _id_match:
            img_base = base_url.replace("http://", "https://", 1)
            img_url = img_base.rsplit("/p/", 1)[0] + "/og/" + _id_match.group(1) + ".gif"
            og_image_tag = (
                f'<meta property="og:image" content="{_esc.escape(img_url)}">\n'
                f'<meta property="og:image:type" content="image/gif">\n'
                f'<meta property="og:image:width" content="1200">\n'
                f'<meta property="og:image:height" content="630">'
            )
            twitter_image_tag = (
                f'<meta name="twitter:image" content="{_esc.escape(img_url)}">'
            )

    poem_html = _poem_to_html(text, morphed)

    title_html = ""
    if has_explicit_title:
        title_html = f'<h1 class="poem-title">{_esc.escape(title)}</h1>'

    # Hidden marker so OG image generator knows if title was explicit
    explicit_marker = (
        f'<meta name="ghostwriter:has-title" content="{"yes" if has_explicit_title else "no"}">'
    )

    return _TEMPLATE.format(
        title=_esc.escape(title),
        og_title=_esc.escape(title),
        og_description=_esc.escape(og_desc),
        og_url_tag=og_url_tag,
        og_image_tag=og_image_tag + "\n" + explicit_marker,
        twitter_image_tag=twitter_image_tag,
        poem_html=poem_html,
        title_html=title_html,
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
