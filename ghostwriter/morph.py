"""Word morphing engine: thesaurus + embedding arithmetic.

Combines WordNet synonym lookups with GloVe vector arithmetic so that
replacement candidates:

  1. Match the original word's part of speech.
  2. Preserve tense / plural form (via lemminflect).
  3. Drift toward a user-supplied "vibe" direction.

The vector arithmetic is:

    target ≈ word + vibe − source_vibe

Candidates from both WordNet and embeddings are scored against this
target vector, then re-inflected to be drop-in replacements.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import gensim.downloader as api
from gensim.models import KeyedVectors

# ---------------------------------------------------------------------------
# Lazy singleton for the embedding model
# ---------------------------------------------------------------------------

_MODEL: Optional[KeyedVectors] = None
_MODEL_NAME = "glove-wiki-gigaword-100"


def load_model(name: str = _MODEL_NAME) -> KeyedVectors:
    """Load (and cache) the word-vector model.  First call downloads ~128 MB."""
    global _MODEL
    if _MODEL is None:
        _MODEL = api.load(name)
    return _MODEL


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class Candidate:
    """A single replacement suggestion for a word."""

    word: str
    score: float  # cosine similarity to target vector


@dataclass
class MorphResult:
    """Result of morphing one word toward a vibe."""

    original: str
    vibe: str
    source_vibe: Optional[str]
    candidates: list[Candidate] = field(default_factory=list)


# ---------------------------------------------------------------------------
# NLTK bootstrap
# ---------------------------------------------------------------------------

_NLTK_READY = False


def _ensure_nltk() -> None:
    """Download required NLTK data on first use."""
    global _NLTK_READY
    if _NLTK_READY:
        return
    import nltk

    for resource, path in [
        ("averaged_perceptron_tagger_eng", "taggers/averaged_perceptron_tagger_eng"),
        ("wordnet", "corpora/wordnet"),
    ]:
        try:
            nltk.data.find(path)
        except LookupError:
            nltk.download(resource, quiet=True)
    _NLTK_READY = True


# ---------------------------------------------------------------------------
# POS helpers
# ---------------------------------------------------------------------------


def _pos_tag_word(
    word: str, context: list[str] | None = None
) -> str | None:
    """Return the Penn Treebank POS tag for *word*.

    When *context* is provided (list of words from the surrounding line /
    sentence), the tagger uses it to disambiguate ambiguous words like
    "understanding" (noun in "a deep understanding" vs. verb in
    "understanding the problem").
    """
    try:
        _ensure_nltk()
        from nltk.tag import pos_tag as _pos_tag

        if context:
            tagged = _pos_tag(context)
            low = word.lower()
            for w, tag in tagged:
                if w.lower() == low:
                    return tag
        # Fallback: tag in isolation
        return _pos_tag([word])[0][1]
    except Exception:
        return None


def _ptb_to_wordnet(tag: str):
    """Convert a Penn Treebank tag to a WordNet POS constant."""
    from nltk.corpus import wordnet

    if tag.startswith("NN"):
        return wordnet.NOUN
    if tag.startswith("VB"):
        return wordnet.VERB
    if tag.startswith("JJ"):
        return wordnet.ADJ
    if tag.startswith("RB"):
        return wordnet.ADV
    return None


def _coarse_pos(tag: str | None) -> str | None:
    """Coarse POS bucket from a PTB tag."""
    if tag is None:
        return None
    if tag.startswith("NN"):
        return "noun"
    if tag.startswith("VB"):
        return "verb"
    if tag.startswith("JJ"):
        return "adj"
    if tag.startswith("RB"):
        return "adv"
    return None


_COARSE_TO_WN: dict[str, str] = {}  # populated on first call


def _can_be_pos(word: str, coarse: str) -> bool:
    """Check if *word* genuinely functions as *coarse* POS via WordNet.

    NLTK's statistical tagger is unreliable on isolated words (e.g. it
    tags "profound" as NN).  WordNet synset lookup is ground truth:
    if a word has zero synsets for a given POS it cannot serve that role.
    """
    from nltk.corpus import wordnet

    if not _COARSE_TO_WN:
        _COARSE_TO_WN.update(
            {"noun": wordnet.NOUN, "verb": wordnet.VERB,
             "adj": wordnet.ADJ, "adv": wordnet.ADV}
        )
    wn_pos = _COARSE_TO_WN.get(coarse)
    if wn_pos is None:
        return True
    return len(wordnet.synsets(word, pos=wn_pos)) > 0


# ---------------------------------------------------------------------------
# WordNet synonyms
# ---------------------------------------------------------------------------


def _wordnet_synonyms(lemma: str, wn_pos) -> set[str]:
    """All single-word synonyms from WordNet for a lemma + POS.

    Includes direct synonyms plus one hop of hypernyms, hyponyms, and
    similar-tos for broader coverage.
    """
    from nltk.corpus import wordnet

    out: set[str] = set()
    for syn in wordnet.synsets(lemma, pos=wn_pos):
        for lem in syn.lemmas():
            w = lem.name().lower()
            if "_" not in w and w != lemma:
                out.add(w)
        # One hop outward for more variety
        related = syn.hypernyms() + syn.hyponyms()
        if hasattr(syn, "similar_tos"):
            related += syn.similar_tos()
        for rel in related:
            for lem in rel.lemmas():
                w = lem.name().lower()
                if "_" not in w and w != lemma:
                    out.add(w)
    return out


# ---------------------------------------------------------------------------
# Lemmatization & inflection
# ---------------------------------------------------------------------------


def _lemmatize(word: str, wn_pos) -> str:
    """Reduce a word to its base form."""
    from nltk.stem import WordNetLemmatizer

    return WordNetLemmatizer().lemmatize(word, pos=wn_pos)


def _inflect(word: str, target_tag: str) -> str:
    """Re-inflect *word* to match *target_tag* (e.g. VBG, NNS).

    Uses lemminflect when available, otherwise returns the word unchanged.
    """
    try:
        import lemminflect

        forms = lemminflect.getInflection(word, tag=target_tag)
        return forms[0] if forms else word
    except (ImportError, Exception):
        return word


# ---------------------------------------------------------------------------
# Core morphing
# ---------------------------------------------------------------------------

_WORD_RE = re.compile(r"^[a-z]+$")


def _in_vocab(model: KeyedVectors, word: str) -> bool:
    return word.lower() in model


def morph_word(
    word: str,
    target_vibe: str,
    source_vibe: str | None = None,
    top_n: int = 8,
    model: KeyedVectors | None = None,
    context: list[str] | None = None,
) -> MorphResult:
    """Shift *word* toward *target_vibe*, returning drop-in replacements.

    Parameters
    ----------
    word:
        The word to morph.
    target_vibe:
        A single word describing the desired feeling (e.g. "dread").
    source_vibe:
        Optional word to subtract from the direction (e.g. "joy").
    top_n:
        How many candidates to return.
    model:
        Pre-loaded KeyedVectors; uses the global singleton when *None*.
    context:
        Words from the surrounding line / sentence.  Passed to the POS
        tagger so that ambiguous words like "understanding" are tagged
        correctly based on how they are actually used.

    Returns
    -------
    MorphResult with ranked candidates that match POS and inflection.
    """
    if model is None:
        model = load_model()

    result = MorphResult(original=word, vibe=target_vibe, source_vibe=source_vibe)

    low = word.lower()
    vibe_low = target_vibe.lower()

    if not _in_vocab(model, low) or not _in_vocab(model, vibe_low):
        return result

    # -- POS & lemma --------------------------------------------------------
    ptb_tag = _pos_tag_word(low, context=context)
    wn_pos = _ptb_to_wordnet(ptb_tag) if ptb_tag else None
    coarse = _coarse_pos(ptb_tag)
    lemma = _lemmatize(low, wn_pos) if wn_pos else low

    # -- Build target vector (word + vibe − source_vibe) --------------------
    target_vec = model[low].astype(np.float64) + model[vibe_low].astype(np.float64)
    if source_vibe and _in_vocab(model, source_vibe.lower()):
        target_vec -= model[source_vibe.lower()].astype(np.float64)
    norm = np.linalg.norm(target_vec)
    if norm > 0:
        target_vec /= norm

    # -- Candidate pool (base-form words) -----------------------------------
    pool: set[str] = set()

    # Source 1: WordNet synonyms — POS-filtered to catch cross-POS leaks
    # from the one-hop expansion (hypernyms / similar-tos).
    if wn_pos:
        wn_cands = _wordnet_synonyms(lemma, wn_pos)
        if coarse:
            wn_cands = {
                w for w in wn_cands
                if _coarse_pos(_pos_tag_word(w)) == coarse
            }
        pool |= wn_cands

    # Source 2: embedding neighbours via vector arithmetic
    positive = [low, vibe_low]
    negative: list[str] = []
    if source_vibe and _in_vocab(model, source_vibe.lower()):
        negative = [source_vibe.lower()]
    try:
        raw = model.most_similar(
            positive=positive,
            negative=negative or None,
            topn=top_n * 5,
        )
        for cand_word, _score in raw:
            cw = cand_word.lower()
            if cw in (low, vibe_low, lemma):
                continue
            if not _WORD_RE.match(cw):
                continue
            # Hard POS filter: only keep same coarse POS
            if coarse:
                cand_tag = _pos_tag_word(cw)
                if _coarse_pos(cand_tag) != coarse:
                    continue
            # Lemmatize so the pool is always base forms
            if wn_pos:
                cw = _lemmatize(cw, wn_pos)
            if cw in (low, vibe_low, lemma):
                continue
            pool.add(cw)
    except KeyError:
        pass

    # -- Score each candidate against the target vector ---------------------
    scored: list[tuple[str, float]] = []
    for cand in pool:
        if not _in_vocab(model, cand):
            continue
        # WordNet cross-check: reject words that cannot genuinely serve
        # the target POS (catches NLTK tagger errors on isolated words).
        if coarse and not _can_be_pos(cand, coarse):
            continue
        cand_vec = model[cand].astype(np.float64)
        cand_norm = np.linalg.norm(cand_vec)
        if cand_norm == 0:
            continue
        sim = float(np.dot(target_vec, cand_vec) / cand_norm)
        scored.append((cand, sim))

    scored.sort(key=lambda x: x[1], reverse=True)

    # -- Inflect to match original form & deduplicate -----------------------
    seen: set[str] = set()
    for cand_lemma, score in scored:
        inflected = _inflect(cand_lemma, ptb_tag) if ptb_tag else cand_lemma
        il = inflected.lower()
        if il in seen or il == low:
            continue
        seen.add(il)
        result.candidates.append(
            Candidate(word=inflected, score=round(score, 4))
        )
        if len(result.candidates) >= top_n:
            break

    return result


def morph_words(
    words: list[str],
    target_vibe: str,
    source_vibe: str | None = None,
    top_n: int = 8,
    contexts: list[list[str]] | None = None,
) -> list[MorphResult]:
    """Morph several words in one call (shares the loaded model).

    *contexts*, when provided, should be a list of word-lists — one per
    entry in *words* — giving the surrounding line so the POS tagger can
    disambiguate.
    """
    model = load_model()
    if contexts is None:
        contexts = [None] * len(words)  # type: ignore[list-item]
    return [
        morph_word(
            w, target_vibe, source_vibe,
            top_n=top_n, model=model, context=ctx,
        )
        for w, ctx in zip(words, contexts)
    ]


# ---------------------------------------------------------------------------
# Emoji morphing
# ---------------------------------------------------------------------------

_EMOJI_STOP = frozenset({
    "face", "with", "and", "of", "in", "on", "the", "a", "an",
    "sign", "mark", "symbol", "button", "type", "skin", "tone",
    "light", "medium", "dark",
})

_EMOJI_INDEX: dict[str, np.ndarray] | None = None
_EMOJI_CHARS: list[str] | None = None


_EMOJI_RANGES = [
    (0x1F300, 0x1F5FF),  # Miscellaneous Symbols and Pictographs
    (0x1F600, 0x1F64F),  # Emoticons
    (0x1F680, 0x1F6FF),  # Transport and Map Symbols
    (0x1F900, 0x1F9FF),  # Supplemental Symbols and Pictographs
    (0x1FA70, 0x1FAFF),  # Symbols and Pictographs Extended-A
    (0x2600, 0x27BF),    # Misc Symbols / Dingbats
    (0x2300, 0x23FF),    # Misc Technical (⌚ etc.)
]


def _build_emoji_index(model: KeyedVectors) -> tuple[dict[str, np.ndarray], list[str]]:
    """Build a mapping from emoji char to its meaning vector."""
    import unicodedata

    index: dict[str, np.ndarray] = {}
    chars: list[str] = []

    for start, end in _EMOJI_RANGES:
        for cp in range(start, end + 1):
            ch = chr(cp)
            try:
                name = unicodedata.name(ch)
            except ValueError:
                continue
            cat = unicodedata.category(ch)
            if cat not in ("So", "Sk"):
                continue

            keywords = [w.lower() for w in name.split() if w.lower() not in _EMOJI_STOP]
            vecs = [model[k].astype(np.float64) for k in keywords if _in_vocab(model, k)]
            if not vecs:
                continue

            mean = np.mean(vecs, axis=0)
            norm = np.linalg.norm(mean)
            if norm > 0:
                mean /= norm
            index[ch] = mean
            chars.append(ch)

    return index, chars


def _get_emoji_index(model: KeyedVectors) -> tuple[dict[str, np.ndarray], list[str]]:
    global _EMOJI_INDEX, _EMOJI_CHARS
    if _EMOJI_INDEX is None:
        _EMOJI_INDEX, _EMOJI_CHARS = _build_emoji_index(model)
    return _EMOJI_INDEX, _EMOJI_CHARS


def is_emoji(text: str) -> bool:
    """Return True if text is a single emoji character."""
    if len(text) != 1:
        return False
    cp = ord(text)
    return (
        0x1F300 <= cp <= 0x1FAFF
        or 0x2600 <= cp <= 0x27BF
        or 0x2300 <= cp <= 0x23FF
    )


def morph_emoji(
    emoji: str,
    target_vibe: str,
    top_n: int = 8,
    model: KeyedVectors | None = None,
) -> MorphResult:
    """Find emojis similar to *emoji* shifted toward *target_vibe*."""
    if model is None:
        model = load_model()

    result = MorphResult(original=emoji, vibe=target_vibe, source_vibe=None)

    index, chars = _get_emoji_index(model)
    if emoji not in index:
        return result

    vibe_low = target_vibe.lower()
    if not _in_vocab(model, vibe_low):
        return result

    source_vec = index[emoji]
    vibe_vec = model[vibe_low].astype(np.float64)
    vibe_norm = np.linalg.norm(vibe_vec)
    if vibe_norm > 0:
        vibe_vec /= vibe_norm

    target_vec = source_vec + vibe_vec
    norm = np.linalg.norm(target_vec)
    if norm > 0:
        target_vec /= norm

    # Score all emojis by cosine similarity to the target
    scored: list[tuple[str, float]] = []
    for ch in chars:
        if ch == emoji:
            continue
        score = float(np.dot(index[ch], target_vec))
        scored.append((ch, score))

    scored.sort(key=lambda x: x[1], reverse=True)

    for ch, score in scored[:top_n]:
        result.candidates.append(Candidate(word=ch, score=round(score, 4)))

    return result
