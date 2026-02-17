# Ghostwriter

A terminal UI for writing poems and prose, morphing their meaning through
word-embedding vector arithmetic, and rendering them as ghosted PDFs for the
Sony DPT-RP1 e-ink reader.

The "ghosting" visual effect — overlapping, slightly jittered text layers —
mirrors the semantic drift of the morphed words.

## Quickstart

```bash
pip install -e .

# Terminal UI (desktop)
ghostwriter

# Web UI (works from your phone too)
ghostwriter-web
```

On first launch the app downloads GloVe word vectors (~128 MB, cached locally).

The web UI runs at `http://localhost:5000` and prints your local IP so you
can open it from your phone on the same WiFi.

## How it works

1. **Write** a poem or paste one into the editor.
2. **Enter a vibe** — a single word like *dread*, *warmth*, *alien*.
3. **Tag words** — place cursor on a word and press **F2** to tag it for morphing. Repeat for as many words as you like.
4. **Cycle** — press **F5**. Tagged words begin cycling through replacement candidates live in the editor.
5. **Lock** — press **F7** on a cycling word to freeze it at the current replacement, or **F6** to freeze all words at once.
6. **Share** — press **F9** to save a web view to `poems/` with a unique ID and open it in your browser. Morphed words show their originals as ghosts on hover. Each page has Open Graph tags for rich iOS Messages / social previews.
7. **Render** a ghosted PDF (**F3**) and **push** it to your DPT-RP1 (**F4**).

### Web UI (phone-friendly)

Run `ghostwriter-web` and open the URL on your phone. The flow:

1. **Write** or paste a poem, enter a vibe word.
2. **Tag** — tap words to mark them for morphing.
3. **Morph** — see candidates, tap morphed words to pick alternatives.
4. **Save & share** — get a unique URL with OG tags for iOS Messages previews.
5. **Gallery** — browse all saved poems at `/poems`.

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
| F9 | Share — save to poems/ + open web view |
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
| [Flask](https://flask.palletsprojects.com/) | Web UI server |

## Deploying to Fly.io

Host the web UI at a custom domain (e.g. `ghostwriter.humble.audio`).

```bash
# 1. Install flyctl — https://fly.io/docs/flyctl/install/
brew install flyctl
fly auth login

# 2. Launch the app (keep the existing fly.toml)
fly launch --copy-config

# 3. Create a persistent volume for poems + model cache
fly volumes create ghostwriter_data --region sjc --size 1

# 4. Set an API key (protects morph + save endpoints)
fly secrets set GHOSTWRITER_API_KEY="$(openssl rand -hex 16)"

# 5. Deploy
fly deploy

# 6. Custom domain
fly certs add ghostwriter.humble.audio
# Then add a CNAME in your DNS:
#   ghostwriter.humble.audio -> <app-name>.fly.dev
```

The GloVe model (~128 MB) downloads on first request and is cached on the
persistent volume. Subsequent cold starts load from disk (~10 s).

Machines auto-stop when idle and wake on the next request.

## License

MIT
