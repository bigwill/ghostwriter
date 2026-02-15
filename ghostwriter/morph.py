"""Embedding-based word morphing engine.

Uses GloVe vectors (via gensim) to shift words toward a target "vibe"
through vector arithmetic:

    morphed ≈ word + target_vibe − source_vibe

"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

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
    score: float  # cosine similarity


@dataclass
class MorphResult:
    """Result of morphing one word toward a vibe."""

    original: str
    vibe: str
    source_vibe: Optional[str]
    candidates: list[Candidate] = field(default_factory=list)


# ---------------------------------------------------------------------------
# POS heuristic (lightweight, no nltk download required at import time)
# ---------------------------------------------------------------------------

try:
    import nltk
    from nltk.tag import pos_tag as _pos_tag

    def _ensure_tagger() -> None:
        try:
            nltk.data.find("taggers/averaged_perceptron_tagger_eng")
        except LookupError:
            nltk.download("averaged_perceptron_tagger_eng", quiet=True)

    def pos_tag(word: str) -> str | None:
        """Return a coarse POS tag (noun / verb / adj / adv) or None."""
        _ensure_tagger()
        tag = _pos_tag([word])[0][1]
        if tag.startswith("NN"):
            return "noun"
        if tag.startswith("VB"):
            return "verb"
        if tag.startswith("JJ"):
            return "adj"
        if tag.startswith("RB"):
            return "adv"
        return None

except ImportError:  # nltk not installed

    def pos_tag(word: str) -> str | None:  # type: ignore[misc]
        return None


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
) -> MorphResult:
    """Shift *word* toward *target_vibe* using vector arithmetic.

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

    Returns
    -------
    MorphResult with ranked candidates.
    """
    if model is None:
        model = load_model()

    result = MorphResult(original=word, vibe=target_vibe, source_vibe=source_vibe)

    low = word.lower()
    vibe_low = target_vibe.lower()

    if not _in_vocab(model, low):
        return result
    if not _in_vocab(model, vibe_low):
        return result

    positive = [low, vibe_low]
    negative = []
    if source_vibe and _in_vocab(model, source_vibe.lower()):
        negative = [source_vibe.lower()]

    try:
        raw = model.most_similar(positive=positive, negative=negative or None, topn=top_n * 3)
    except KeyError:
        return result

    # Determine the original word's coarse POS so we can prefer same-POS matches.
    orig_pos = pos_tag(low)

    seen: set[str] = set()
    for candidate_word, score in raw:
        # Keep only clean single words, skip the input itself.
        cw = candidate_word.lower()
        if cw in (low, vibe_low):
            continue
        if not _WORD_RE.match(cw):
            continue
        if cw in seen:
            continue
        seen.add(cw)

        # Soft POS filter: prefer same POS but don't hard-reject.
        cand_pos = pos_tag(cw)
        if orig_pos and cand_pos and cand_pos != orig_pos:
            score *= 0.85  # demote slightly

        result.candidates.append(Candidate(word=cw, score=round(score, 4)))
        if len(result.candidates) >= top_n:
            break

    # Re-sort after POS adjustment.
    result.candidates.sort(key=lambda c: c.score, reverse=True)
    return result


def morph_words(
    words: list[str],
    target_vibe: str,
    source_vibe: str | None = None,
    top_n: int = 8,
) -> list[MorphResult]:
    """Morph several words in one call (shares the loaded model)."""
    model = load_model()
    return [
        morph_word(w, target_vibe, source_vibe, top_n=top_n, model=model)
        for w in words
    ]
