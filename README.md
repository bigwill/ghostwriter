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
3. **Place cursor** on a word and press **F2** to morph it.
4. **Pick** a replacement from the ranked candidates.
5. **Render** a ghosted PDF (**F3**) and **push** it to your DPT-RP1 (**F4**).

## Dependencies

| Package | Role |
|---------|------|
| [Textual](https://github.com/Textualize/textual) | Terminal UI |
| [gensim](https://radimrehurek.com/gensim/) | Word embeddings (GloVe) |
| [reportlab](https://docs.reportlab.com/) | PDF generation |
| [dpt-rp1-py](https://github.com/janten/dpt-rp1-py) | Sony DPT-RP1 integration |
| [nltk](https://www.nltk.org/) | Part-of-speech tagging |

## License

MIT
