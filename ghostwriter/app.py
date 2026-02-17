"""Ghostwriter – Textual TUI application.

Run with:  ghostwriter   (or  python -m ghostwriter.app)
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import (
    Footer,
    Header,
    Input,
    Label,
    OptionList,
    Static,
    TextArea,
)
from textual.widgets.option_list import Option
from textual.worker import Worker, WorkerState

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

_WORD_RE = re.compile(r"[A-Za-z]+")

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

#word-label {
    color: $text;
    text-style: bold;
    margin-bottom: 1;
}

#cand-label {
    color: $text-muted;
    margin-bottom: 0;
}

#candidates {
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
        Binding("f2", "morph_word", "Morph word"),
        Binding("f3", "render_pdf", "Render PDF"),
        Binding("f4", "push_device", "Push to DPT-RP1"),
        Binding("ctrl+s", "save_poem", "Save poem"),
        Binding("ctrl+o", "load_poem", "Load poem"),
        Binding("ctrl+q", "quit", "Quit"),
    ]

    def __init__(self) -> None:
        super().__init__()
        # word -> replacement mapping
        self.morphed: dict[str, str] = {}
        # currently selected word for morphing
        self._selected_word: Optional[str] = None
        # model loaded flag
        self._model_ready = False

    # ---- layout ----------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="main"):
            with Vertical(id="editor-pane"):
                yield TextArea(id="editor", language=None, soft_wrap=True)
            with Vertical(id="side-pane"):
                yield Label("1. enter a vibe direction", id="vibe-label")
                yield Input(
                    placeholder="e.g. dread, warmth, alien",
                    id="vibe-input",
                )
                yield Label(
                    "2. place cursor on a word, press [bold]F2[/bold]",
                    id="word-label",
                )
                yield Label("3. pick a replacement below", id="cand-label")
                yield OptionList(id="candidates")
                with VerticalScroll(id="morphed-section"):
                    yield Label("morphed words", id="morphed-header")
                    yield Static("(none yet)", id="morphed-list")
        yield Static("Loading embeddings…", id="status-bar")
        yield Footer()

    # ---- lifecycle -------------------------------------------------------

    def on_mount(self) -> None:
        self._load_model()

    @work(thread=True, group="model")
    def _load_model(self) -> None:
        """Download / cache the GloVe model in a background thread."""
        from ghostwriter.morph import load_model  # noqa: delayed import

        load_model()
        self._model_ready = True

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.group == "model":
            bar = self.query_one("#status-bar", Static)
            if event.state == WorkerState.RUNNING:
                bar.update("Loading embeddings (first run downloads ~128 MB)…")
            elif event.state == WorkerState.SUCCESS:
                bar.update("Ready — write a poem, enter a vibe, put cursor on a word, press F2")
            elif event.state == WorkerState.ERROR:
                bar.update(f"Model load failed: {event.worker.error}")

    # ---- word selection --------------------------------------------------

    def action_morph_word(self) -> None:
        """Pick the word under the cursor in the editor and show candidates."""
        editor = self.query_one("#editor", TextArea)
        text = editor.text
        if not text:
            return

        # Get cursor position and find the word at that location
        row, col = editor.cursor_location
        lines = text.splitlines()
        if row >= len(lines):
            return
        line = lines[row]

        # Find the word surrounding the cursor column
        for m in _WORD_RE.finditer(line):
            if m.start() <= col <= m.end():
                word = m.group()
                self._selected_word = word
                self.query_one("#word-label", Label).update(
                    f"morphing: [bold]{word}[/bold]  (F2 to pick another)"
                )
                self._fetch_candidates(word)
                return

        self.query_one("#status-bar", Static).update(
            "Place cursor on a word first"
        )

    def _fetch_candidates(self, word: str) -> None:
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

        self._run_morph(word, vibe)

    @work(thread=True, group="morph")
    def _run_morph(self, word: str, vibe: str) -> list:
        from ghostwriter.morph import morph_word

        result = morph_word(word, vibe)
        return result.candidates  # type: ignore[return-value]

    @on(Worker.StateChanged)
    def _morph_done(self, event: Worker.StateChanged) -> None:
        if event.worker.group != "morph":
            return
        if event.state == WorkerState.SUCCESS:
            candidates = event.worker.result
            opt = self.query_one("#candidates", OptionList)
            opt.clear_options()
            if not candidates:
                opt.add_option(Option("(no candidates found)"))
                return
            for c in candidates:
                opt.add_option(
                    Option(f"{c.word}  ({c.score:.3f})", id=c.word)
                )
        elif event.state == WorkerState.ERROR:
            self.query_one("#status-bar", Static).update(
                f"Morph error: {event.worker.error}"
            )

    # ---- accept a candidate ----------------------------------------------

    @on(OptionList.OptionSelected, "#candidates")
    def _accept_candidate(self, event: OptionList.OptionSelected) -> None:
        if self._selected_word is None:
            return
        replacement = event.option.id
        if replacement is None or replacement.startswith("("):
            return

        original = self._selected_word

        # Replace in the editor text
        editor = self.query_one("#editor", TextArea)
        old_text = editor.text

        # Replace the first occurrence of the selected word (whole-word)
        pattern = re.compile(r"\b" + re.escape(original) + r"\b")
        new_text = pattern.sub(replacement, old_text, count=1)
        editor.load_text(new_text)

        # Track the morph
        self.morphed[original.lower()] = replacement.lower()
        self._refresh_morphed_list()
        self._selected_word = None
        self.query_one("#word-label", Label).update(
            "2. place cursor on a word, press [bold]F2[/bold]"
        )
        self.query_one("#candidates", OptionList).clear_options()
        self.query_one("#status-bar", Static).update(
            f"'{original}' → '{replacement}'"
        )

    def _refresh_morphed_list(self) -> None:
        if not self.morphed:
            self.query_one("#morphed-list", Static).update("(none yet)")
            return
        lines = [f"  {k} → {v}" for k, v in self.morphed.items()]
        self.query_one("#morphed-list", Static).update("\n".join(lines))

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
        path = render_poem(lines, output="ghost.pdf", morphed_words=morphed_set)
        return path

    @on(Worker.StateChanged)
    def _render_done(self, event: Worker.StateChanged) -> None:
        if event.worker.group != "render":
            return
        if event.state == WorkerState.SUCCESS:
            path = event.worker.result
            self.query_one("#status-bar", Static).update(
                f"PDF saved → {path}"
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

    # ---- save / load poem ------------------------------------------------

    def action_save_poem(self) -> None:
        editor = self.query_one("#editor", TextArea)
        text = editor.text
        Path("poem.txt").write_text(text, encoding="utf-8")
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
        self._refresh_morphed_list()
        self.query_one("#status-bar", Static).update("Loaded poem.txt")


# ------------------------------------------------------------------
# Entry point
# ------------------------------------------------------------------


def main() -> None:
    app = GhostwriterApp()
    app.run()


if __name__ == "__main__":
    main()
