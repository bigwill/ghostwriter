# Ghostwriter

A terminal UI for writing poems and prose, morphing their meaning through
word-embedding vector arithmetic, and rendering them as ghosted PDFs for the
Sony DPT-RP1 e-ink reader.

The "ghosting" visual effect — overlapping, slightly jittered text layers —
mirrors the semantic drift of the morphed words.

## Quickstart

```bash
pip install -e .
ghostwriter
```

On first launch the app downloads GloVe word vectors (~128 MB, cached locally).

## How it works

1. **Write** a poem or paste one into the editor.
2. **Enter a vibe** — a single word like *dread*, *warmth*, *alien*.
3. **Tag words** — place cursor on a word and press **F2** to tag it for morphing. Repeat for as many words as you like.
4. **Cycle** — press **F5**. Tagged words begin cycling through replacement candidates live in the editor.
5. **Lock** — press **F7** on a cycling word to freeze it at the current replacement, or **F6** to freeze all words at once.
6. **Render** a ghosted PDF (**F3**) and **push** it to your DPT-RP1 (**F4**).

Replacements are drop-in: they match the original word's part of speech and
tense/inflection, ranked by proximity to the vibe direction.

## Key bindings

| Key | Action |
|-----|--------|
| F2 | Tag / untag word under cursor (works mid-cycle too) |
| F5 | Start cycling tagged words |
| Escape | Stop cycling (freeze all at current candidates) |
| F6 | Freeze all cycling words |
| F7 | Lock individual word under cursor |
| F8 | Toggle yellow highlight on cycling words |
| F3 | Render ghosted PDF |
| F4 | Push PDF to DPT-RP1 |
| Ctrl+S | Save poem |
| Ctrl+O | Load poem |
| Ctrl+Q | Quit |

## Dependencies

| Package | Role |
|---------|------|
| [Textual](https://github.com/Textualize/textual) | Terminal UI |
| [gensim](https://radimrehurek.com/gensim/) | Word embeddings (GloVe) |
| [reportlab](https://docs.reportlab.com/) | PDF generation |
| [dpt-rp1-py](https://github.com/janten/dpt-rp1-py) | Sony DPT-RP1 integration |
| [nltk](https://www.nltk.org/) | Part-of-speech tagging + WordNet thesaurus |
| [lemminflect](https://github.com/bjascob/LemmInflect) | Inflection matching |

## License

MIT
