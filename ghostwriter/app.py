"""Ghostwriter – Textual TUI application.

Run with:  ghostwriter   (or  python -m ghostwriter.app)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.timer import Timer
from textual.widgets import (
    Footer,
    Header,
    Input,
    Label,
    Static,
    TextArea,
)
from textual.worker import Worker, WorkerState

# ---------------------------------------------------------------------------
# Token model
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"[A-Za-z]+")


@dataclass
class Token:
    """A piece of text in the poem — either a word or non-word."""

    text: str
    is_word: bool = False
    tagged: bool = False
    candidates: list[str] = field(default_factory=list)
    cycle_index: int = 0
    locked: bool = False

    @property
    def display(self) -> str:
        if self.tagged and self.candidates:
            return self.candidates[self.cycle_index]
        return self.text


def _tokenize_line(line: str) -> list[Token]:
    """Split a line into alternating word / non-word tokens."""
    tokens: list[Token] = []
    last = 0
    for m in _TOKEN_RE.finditer(line):
        if m.start() > last:
            tokens.append(Token(text=line[last : m.start()]))
        tokens.append(Token(text=m.group(), is_word=True))
        last = m.end()
    if last < len(line):
        tokens.append(Token(text=line[last:]))
    if not tokens:
        tokens.append(Token(text=""))
    return tokens


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

CSS = """\
#main {
    height: 1fr;
}

#editor-pane {
    width: 2fr;
    border: solid $accent;
    height: 100%;
}

#editor-pane TextArea {
    height: 1fr;
}

#side-pane {
    width: 1fr;
    border: solid $accent;
    padding: 1 2;
    height: 100%;
}

#side-pane Label {
    margin-bottom: 1;
}

#vibe-input {
    margin-bottom: 1;
}

#help-label {
    color: $text-muted;
    margin-bottom: 1;
}

#tagged-list {
    height: 1fr;
    min-height: 6;
}

#morphed-section {
    height: auto;
    max-height: 40%;
    border-top: solid $accent;
    padding-top: 1;
    margin-top: 1;
}

#morphed-list {
    height: auto;
    max-height: 100%;
}

#status-bar {
    dock: bottom;
    height: 1;
    background: $accent;
    color: $text;
    padding: 0 2;
}
"""


class GhostwriterApp(App):
    """Main TUI for writing, morphing, and rendering ghosted poems."""

    TITLE = "Ghostwriter"
    SUB_TITLE = "morph → ghost → ink"

    CSS = CSS

    BINDINGS = [
        Binding("f2", "tag_word", "Tag word"),
        Binding("f5", "start_cycling", "Cycle"),
        Binding("f6", "freeze_all", "Freeze all"),
        Binding("f7", "lock_word", "Lock word"),
        Binding("f8", "toggle_highlight", "Highlight"),
        Binding("escape", "stop_cycling", "Stop", show=False),
        Binding("f3", "render_pdf", "Render PDF"),
        Binding("f4", "push_device", "Push to DPT-RP1"),
        Binding("f9", "share_poem", "Share"),
        Binding("ctrl+s", "save_poem", "Save poem"),
        Binding("ctrl+o", "load_poem", "Load poem"),
        Binding("ctrl+q", "quit", "Quit"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.morphed: dict[str, str] = {}
        self._model_ready = False
        self._poem_tokens: list[list[Token]] = []
        self._tokenized_text: str = ""
        self._cycling = False
        self._cycle_timer: Optional[Timer] = None
        self._highlight: bool = True

    # ---- layout ----------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="main"):
            with Vertical(id="editor-pane"):
                yield TextArea(id="editor", language=None, soft_wrap=True)
            with Vertical(id="side-pane"):
                yield Label("1. enter a vibe", id="vibe-label")
                yield Input(
                    placeholder="e.g. dread, warmth, alien",
                    id="vibe-input",
                )
                yield Label(
                    "F2=tag  F5=cycle  F7=lock  Esc/F6=stop  F8=highlight",
                    id="help-label",
                )
                yield Label("tagged words", id="tagged-header")
                yield Static("(none)", id="tagged-list")
                with VerticalScroll(id="morphed-section"):
                    yield Label("morphed words", id="morphed-header")
                    yield Static("(none yet)", id="morphed-list")
        yield Static("Loading embeddings…", id="status-bar")
        yield Footer()

    # ---- lifecycle -------------------------------------------------------

    def on_mount(self) -> None:
        self._load_model()
        self._cycle_timer = self.set_interval(
            2.5, self._cycle_tick, pause=True
        )

    @work(thread=True, group="model")
    def _load_model(self) -> None:
        """Download / cache the GloVe model in a background thread."""
        from ghostwriter.morph import load_model

        load_model()
        self._model_ready = True

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.group == "model":
            bar = self.query_one("#status-bar", Static)
            if event.state == WorkerState.RUNNING:
                bar.update("Loading embeddings (first run downloads ~128 MB)…")
            elif event.state == WorkerState.SUCCESS:
                bar.update("Ready — F2 to tag words, F5 to cycle")
            elif event.state == WorkerState.ERROR:
                bar.update(f"Model load failed: {event.worker.error}")

    # ---- tokenizer -------------------------------------------------------

    def _ensure_tokenized(self) -> None:
        """Tokenize the editor text if it changed since last tokenization."""
        editor = self.query_one("#editor", TextArea)
        text = editor.text
        if text != self._tokenized_text:
            self._poem_tokens = [
                _tokenize_line(line) for line in text.splitlines()
            ]
            self._tokenized_text = text

    def _editor_display(self, t: Token) -> str:
        """How a token renders in the editor during cycling.

        During cycling, tagged words are padded to the width of the longest
        candidate (including the original word) so the layout stays stable.
        When highlight is on, unlocked cycling words get «guillemet» markers.
        """
        if self._cycling and t.tagged and t.candidates:
            word = t.candidates[t.cycle_index]
            max_len = max(len(t.text), *(len(c) for c in t.candidates))
            padded = word.ljust(max_len)
            if self._highlight and not t.locked:
                return f"«{padded}»"
            return padded
        return t.display

    def _rebuild_text(self) -> str:
        """Rebuild display text from tokens (padded + markers during cycling)."""
        return "\n".join(
            "".join(self._editor_display(t) for t in line_tokens)
            for line_tokens in self._poem_tokens
        )

    def _final_text(self) -> str:
        """Rebuild text with actual replacements — no padding or markers."""
        return "\n".join(
            "".join(t.display for t in line_tokens)
            for line_tokens in self._poem_tokens
        )

    def _refresh_editor(self) -> None:
        """Reload editor text from tokens, preserving cursor position."""
        editor = self.query_one("#editor", TextArea)
        cursor = editor.cursor_location
        editor.load_text(self._rebuild_text())
        new_lines = editor.text.splitlines()
        if new_lines:
            row = min(cursor[0], len(new_lines) - 1)
            col = min(cursor[1], len(new_lines[row]))
        else:
            row, col = 0, 0
        editor.cursor_location = (row, col)

    def _token_at_cursor(self) -> Optional[Token]:
        """Find the word Token at the current cursor position."""
        editor = self.query_one("#editor", TextArea)
        row, col = editor.cursor_location
        if row >= len(self._poem_tokens):
            return None
        pos = 0
        for token in self._poem_tokens[row]:
            end_pos = pos + len(self._editor_display(token))
            if pos <= col < end_pos and token.is_word:
                return token
            pos = end_pos
        return None

    def _tagged_words(self) -> list[Token]:
        """All currently tagged tokens."""
        return [
            t
            for line_tokens in self._poem_tokens
            for t in line_tokens
            if t.tagged
        ]

    def _line_context(self, token: Token) -> list[str]:
        """Return the words from the line containing *token*.

        This gives the POS tagger surrounding context so it can
        disambiguate words like "understanding" (noun vs. verb).
        """
        for line_tokens in self._poem_tokens:
            if token in line_tokens:
                return [t.text.lower() for t in line_tokens if t.is_word]
        return []

    # ---- tag / untag (F2) ------------------------------------------------

    def action_tag_word(self) -> None:
        """Toggle the tagged state of the word under the cursor.

        Works both outside and during cycling:
        - During cycling, untagging a word commits its current candidate.
        - During cycling, tagging a new word launches a background morph
          and adds it to the cycle once candidates arrive.
        """
        if self._cycling:
            token = self._token_at_cursor()
            if token is None:
                self.query_one("#status-bar", Static).update(
                    "Place cursor on a word"
                )
                return

            if token.tagged:
                # Untag: commit the current candidate and remove from cycling
                current = (
                    token.candidates[token.cycle_index]
                    if token.candidates
                    else token.text
                )
                self.morphed[token.text.lower()] = current.lower()
                token.text = current
                token.tagged = False
                token.candidates = []
                token.cycle_index = 0
                token.locked = False
                self._refresh_morphed_list()
                self.query_one("#status-bar", Static).update(
                    f"Committed: {current}"
                )

                still_cycling = [
                    t
                    for t in self._tagged_words()
                    if not t.locked and t.candidates
                ]
                if not still_cycling:
                    self._finish_cycling()
                else:
                    self._refresh_editor()
                self._refresh_tagged_panel()
            else:
                # Tag a new word and compute morphs in the background
                if not token.is_word:
                    return
                token.tagged = True
                vibe = self.query_one("#vibe-input", Input).value.strip()
                if vibe:
                    ctx = self._line_context(token)
                    self.query_one("#status-bar", Static).update(
                        f"Tagged: {token.text} — computing morphs…"
                    )
                    self._run_add_morph(token.text.lower(), vibe, ctx)
                self._refresh_tagged_panel()
            return

        # -- not cycling: normal tag / untag --------------------------------
        self._ensure_tokenized()
        token = self._token_at_cursor()
        if token is None:
            self.query_one("#status-bar", Static).update(
                "Place cursor on a word"
            )
            return

        token.tagged = not token.tagged
        verb = "Tagged" if token.tagged else "Untagged"
        self.query_one("#status-bar", Static).update(f"{verb}: {token.text}")
        self._refresh_tagged_panel()

    # ---- stop cycling (Escape) -------------------------------------------

    def action_stop_cycling(self) -> None:
        """Stop cycling — freeze all words at their current candidate."""
        if self._cycling:
            self.action_freeze_all()

    # ---- start cycling (F5) ----------------------------------------------

    def action_start_cycling(self) -> None:
        """Compute morphs for all tagged words, then start the cycle timer."""
        if self._cycling:
            return

        if not self._model_ready:
            self.query_one("#status-bar", Static).update(
                "Model still loading…"
            )
            return

        vibe = self.query_one("#vibe-input", Input).value.strip()
        if not vibe:
            self.query_one("#status-bar", Static).update(
                "Enter a vibe word first"
            )
            return

        self._ensure_tokenized()
        tagged = self._tagged_words()
        if not tagged:
            self.query_one("#status-bar", Static).update(
                "Tag some words first (F2)"
            )
            return

        words = [t.text.lower() for t in tagged]
        contexts = [self._line_context(t) for t in tagged]
        self.query_one("#status-bar", Static).update(
            f"Computing morphs for {len(words)} words…"
        )
        self._run_batch_morph(words, vibe, contexts)

    @work(thread=True, group="morph_batch")
    def _run_batch_morph(
        self, words: list[str], vibe: str, contexts: list[list[str]]
    ) -> list:
        from ghostwriter.morph import morph_words

        return morph_words(words, vibe, contexts=contexts)

    @on(Worker.StateChanged)
    def _batch_morph_done(self, event: Worker.StateChanged) -> None:
        if event.worker.group != "morph_batch":
            return
        if event.state == WorkerState.SUCCESS:
            results = event.worker.result
            tagged = self._tagged_words()

            for token, morph_result in zip(tagged, results):
                token.candidates = [c.word for c in morph_result.candidates]
                token.cycle_index = 0
                token.locked = False

            # Untag words that got no candidates
            for t in tagged:
                if not t.candidates:
                    t.tagged = False

            active = [t for t in tagged if t.candidates]
            if not active:
                self.query_one("#status-bar", Static).update(
                    "No candidates found for any tagged word"
                )
                self._refresh_tagged_panel()
                return

            self._cycling = True
            editor = self.query_one("#editor", TextArea)
            editor.read_only = True
            editor.load_text(self._rebuild_text())
            if self._cycle_timer:
                self._cycle_timer.resume()
            self.query_one("#status-bar", Static).update(
                f"Cycling {len(active)} words — F7=lock  F6=freeze  F8=highlight"
            )
            self._refresh_tagged_panel()

        elif event.state == WorkerState.ERROR:
            self.query_one("#status-bar", Static).update(
                f"Morph error: {event.worker.error}"
            )

    # ---- add single word mid-cycle (background morph) --------------------

    @work(thread=True, group="morph_add")
    def _run_add_morph(self, word_lower: str, vibe: str, context: list[str] | None = None):
        from ghostwriter.morph import morph_word

        return morph_word(word_lower, vibe, context=context)

    @on(Worker.StateChanged)
    def _add_morph_done(self, event: Worker.StateChanged) -> None:
        if event.worker.group != "morph_add":
            return
        if event.state == WorkerState.SUCCESS:
            result = event.worker.result
            # Find the matching token (tagged, no candidates yet)
            for line_tokens in self._poem_tokens:
                for token in line_tokens:
                    if (
                        token.tagged
                        and not token.candidates
                        and token.text.lower() == result.original
                    ):
                        token.candidates = [
                            c.word for c in result.candidates
                        ]
                        token.cycle_index = 0
                        token.locked = False
                        if not token.candidates:
                            token.tagged = False
                            self.query_one("#status-bar", Static).update(
                                f"No candidates for: {token.text}"
                            )
                        else:
                            self.query_one("#status-bar", Static).update(
                                f"Now cycling: {token.text}"
                            )
                        break
            if self._cycling:
                self._refresh_editor()
            self._refresh_tagged_panel()
        elif event.state == WorkerState.ERROR:
            self.query_one("#status-bar", Static).update(
                f"Morph error: {event.worker.error}"
            )

    # ---- cycling timer ---------------------------------------------------

    def _cycle_tick(self) -> None:
        """Advance each unlocked tagged word to its next candidate."""
        if not self._cycling:
            return
        changed = False
        for line_tokens in self._poem_tokens:
            for token in line_tokens:
                if token.tagged and not token.locked and token.candidates:
                    token.cycle_index = (
                        (token.cycle_index + 1) % len(token.candidates)
                    )
                    changed = True
        if changed:
            self._refresh_editor()
            self._refresh_tagged_panel()

    # ---- lock word (F7) --------------------------------------------------

    def action_lock_word(self) -> None:
        """Lock the cycling word under the cursor at its current replacement."""
        if not self._cycling:
            return

        token = self._token_at_cursor()
        if token is None or not token.tagged or token.locked:
            self.query_one("#status-bar", Static).update(
                "Place cursor on a cycling word"
            )
            return

        token.locked = True
        self.morphed[token.text.lower()] = token.display.lower()
        self._refresh_morphed_list()
        self.query_one("#status-bar", Static).update(
            f"Locked: {token.text} → {token.display}"
        )

        still_cycling = [
            t
            for t in self._tagged_words()
            if not t.locked and t.candidates
        ]
        if not still_cycling:
            self._finish_cycling()
        self._refresh_tagged_panel()

    # ---- freeze all (F6) -------------------------------------------------

    def action_freeze_all(self) -> None:
        """Lock all cycling words at their current replacement and stop."""
        if not self._cycling:
            return

        for token in self._tagged_words():
            if not token.locked and token.candidates:
                token.locked = True
                self.morphed[token.text.lower()] = token.display.lower()

        self._refresh_morphed_list()
        self._finish_cycling()

    def _finish_cycling(self) -> None:
        """Stop cycling and return to editing mode."""
        self._cycling = False
        if self._cycle_timer:
            self._cycle_timer.pause()

        editor = self.query_one("#editor", TextArea)
        editor.read_only = False
        final_text = self._final_text()
        editor.load_text(final_text)

        self._poem_tokens = []
        self._tokenized_text = ""

        self.query_one("#status-bar", Static).update(
            "Frozen — F2 to tag more words, F3 to render PDF"
        )
        self._refresh_tagged_panel()

    # ---- side panel ------------------------------------------------------

    def _refresh_tagged_panel(self) -> None:
        tagged = self._tagged_words()
        panel = self.query_one("#tagged-list", Static)
        if not tagged:
            panel.update("(none)")
            return
        lines = []
        for t in tagged:
            current = t.display
            if t.locked:
                lines.append(f"  {t.text} → [bold green]{current}[/] ✓")
            elif t.candidates:
                if self._highlight:
                    lines.append(
                        f"  {t.text} → [bold yellow]{current}[/]"
                    )
                else:
                    lines.append(f"  {t.text} → {current}")
            else:
                lines.append(f"  {t.text} (waiting…)")
        panel.update("\n".join(lines))

    def _refresh_morphed_list(self) -> None:
        if not self.morphed:
            self.query_one("#morphed-list", Static).update("(none yet)")
            return
        lines = [f"  {k} → {v}" for k, v in self.morphed.items()]
        self.query_one("#morphed-list", Static).update("\n".join(lines))

    # ---- highlight toggle (F8) -------------------------------------------

    def action_toggle_highlight(self) -> None:
        """Toggle yellow highlighting of cycling words."""
        self._highlight = not self._highlight
        state = "on" if self._highlight else "off"
        self.query_one("#status-bar", Static).update(f"Highlight: {state}")
        if self._cycling:
            self._refresh_editor()
            self._refresh_tagged_panel()

    # ---- render / push ---------------------------------------------------

    def action_render_pdf(self) -> None:
        editor = self.query_one("#editor", TextArea)
        text = editor.text.strip()
        if not text:
            self.query_one("#status-bar", Static).update("Nothing to render")
            return
        self._do_render(text)

    @work(thread=True, group="render")
    def _do_render(self, text: str) -> Path:
        from ghostwriter.render import render_poem

        lines = text.splitlines()
        morphed_set = set(self.morphed.values())
        path = render_poem(
            lines, output="ghost.pdf", morphed_words=morphed_set
        )
        return path

    @on(Worker.StateChanged)
    def _render_done(self, event: Worker.StateChanged) -> None:
        if event.worker.group != "render":
            return
        if event.state == WorkerState.SUCCESS:
            self.query_one("#status-bar", Static).update(
                f"PDF saved → {event.worker.result}"
            )
        elif event.state == WorkerState.ERROR:
            self.query_one("#status-bar", Static).update(
                f"Render failed: {event.worker.error}"
            )

    def action_push_device(self) -> None:
        self._do_push()

    @work(thread=True, group="push")
    def _do_push(self) -> str:
        from ghostwriter.device import upload

        remote = upload("ghost.pdf")
        return remote

    @on(Worker.StateChanged)
    def _push_done(self, event: Worker.StateChanged) -> None:
        if event.worker.group != "push":
            return
        if event.state == WorkerState.SUCCESS:
            self.query_one("#status-bar", Static).update(
                f"Pushed → {event.worker.result}"
            )
        elif event.state == WorkerState.ERROR:
            self.query_one("#status-bar", Static).update(
                f"Push failed: {event.worker.error}"
            )

    # ---- share (web view) ------------------------------------------------

    def action_share_poem(self) -> None:
        """Generate a web view of the poem and serve it locally."""
        editor = self.query_one("#editor", TextArea)
        text = editor.text.strip()
        if not text:
            self.query_one("#status-bar", Static).update("Nothing to share")
            return
        self._do_share(text)

    @work(thread=True, group="share")
    def _do_share(self, text: str) -> str:
        import webbrowser
        from ghostwriter.web import render_poem_html, save_html, start_server

        page = render_poem_html(text, morphed=dict(self.morphed))
        save_html(page, "poem.html")
        url = start_server(page)
        webbrowser.open(url)
        return url

    @on(Worker.StateChanged)
    def _share_done(self, event: Worker.StateChanged) -> None:
        if event.worker.group != "share":
            return
        if event.state == WorkerState.SUCCESS:
            url = event.worker.result
            self.query_one("#status-bar", Static).update(
                f"Sharing → {url}  (saved poem.html)"
            )
        elif event.state == WorkerState.ERROR:
            self.query_one("#status-bar", Static).update(
                f"Share failed: {event.worker.error}"
            )

    # ---- save / load poem ------------------------------------------------

    def action_save_poem(self) -> None:
        editor = self.query_one("#editor", TextArea)
        Path("poem.txt").write_text(editor.text, encoding="utf-8")
        self.query_one("#status-bar", Static).update("Saved → poem.txt")

    def action_load_poem(self) -> None:
        p = Path("poem.txt")
        if not p.exists():
            self.query_one("#status-bar", Static).update(
                "No poem.txt found in current directory"
            )
            return
        editor = self.query_one("#editor", TextArea)
        editor.load_text(p.read_text(encoding="utf-8"))
        self.morphed.clear()
        self._poem_tokens = []
        self._tokenized_text = ""
        self._refresh_morphed_list()
        self._refresh_tagged_panel()
        self.query_one("#status-bar", Static).update("Loaded poem.txt")


# ------------------------------------------------------------------
# Entry point
# ------------------------------------------------------------------


def main() -> None:
    app = GhostwriterApp()
    app.run()


if __name__ == "__main__":
    main()
