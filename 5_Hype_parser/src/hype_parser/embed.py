"""Local embeddings for the diffusion engine (Features v0.2 section 1.1).

Embeddings run **locally** (no LLM, no paid API) — they are the synonymy/similarity tool.
The model is a deferred parameter (the Features section 5 ``embed model`` knob), frozen per
registry version once chosen.

This module is pluggable:
  - If ``sentence_transformers`` is importable, ``get_embedder`` returns a real semantic
    encoder (the intended production path; a one-line swap once installed).
  - Otherwise it falls back to ``HashingEmbedder`` — a deterministic, dependency-free
    word/bigram hashing vectorizer. This gives real cosine geometry (LEXICAL, not semantic)
    so the whole pipeline runs today; it is NOT the production model and is labelled as such
    everywhere it surfaces (logs + the HTML report banner).
"""

import hashlib
import importlib.util
import logging
import re

import numpy as np

log = logging.getLogger(__name__)

DEFAULT_DIM = 256
DEFAULT_ST_MODEL = "all-MiniLM-L6-v2"

_WORD = re.compile(r"[a-z0-9]+")


def _tokens(text: str):
    words = _WORD.findall((text or "").lower())
    grams = list(words)
    grams += [f"{words[i]}_{words[i + 1]}" for i in range(len(words) - 1)]
    return grams


def _l2_normalize(arr: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return arr / norms


class HashingEmbedder:
    """Deterministic word/bigram hashing -> L2-normalized vector. Placeholder, not semantic."""

    semantic = False

    def __init__(self, dim: int = DEFAULT_DIM):
        self.dim = dim
        self.name = f"hashing-v1-d{dim}"

    def encode(self, texts) -> np.ndarray:
        arr = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, t in enumerate(texts):
            for tok in _tokens(t):
                h = int(hashlib.md5(tok.encode("utf-8")).hexdigest(), 16)
                idx = h % self.dim
                sign = 1.0 if (h >> 8) & 1 == 0 else -1.0
                arr[i, idx] += sign
        return _l2_normalize(arr)


class SentenceTransformerEmbedder:
    """Real local semantic encoder (production path)."""

    semantic = True

    def __init__(self, model_name: str = DEFAULT_ST_MODEL):
        self.model = self._load(model_name)
        self.dim = int(self.model.get_sentence_embedding_dimension())
        self.name = f"st:{model_name}"

    @staticmethod
    def _load(model_name: str):
        """Load from the local HF cache first so routine runs are fully offline (no per-run HF
        revalidation HEADs, resilient to HF rate-limits/outages). Network-download only on a cache
        miss (first ever use of a model)."""
        from sentence_transformers import SentenceTransformer  # noqa: local import
        try:
            return SentenceTransformer(model_name, local_files_only=True)
        except Exception as exc:
            log.info("model %r not in local cache (%s); downloading once from HF", model_name, exc)
            return SentenceTransformer(model_name)

    def encode(self, texts) -> np.ndarray:
        vecs = self.model.encode(
            list(texts), normalize_embeddings=True, convert_to_numpy=True,
            show_progress_bar=False,
        )
        return vecs.astype(np.float32)


def get_embedder(model: str | None = None):
    """Return the best available embedder. ``model`` forces a sentence-transformers model."""
    if importlib.util.find_spec("sentence_transformers"):
        try:
            return SentenceTransformerEmbedder(model or DEFAULT_ST_MODEL)
        except Exception as exc:  # corrupt install / no model cached -> fall back
            log.warning("sentence-transformers present but unusable (%s); using hashing", exc)
    if model:
        log.warning("requested model %r but sentence-transformers not installed; "
                    "using hashing fallback", model)
    return HashingEmbedder()


def centroid(vectors: np.ndarray) -> np.ndarray:
    """Mean of (normalized) vectors, renormalized — the sub-theme centroid (Features 1.1)."""
    c = vectors.mean(axis=0)
    n = np.linalg.norm(c)
    return (c / n).astype(np.float32) if n else c.astype(np.float32)


def to_blob(vec: np.ndarray) -> bytes:
    return np.asarray(vec, dtype=np.float32).tobytes()


def from_blob(blob: bytes, dim: int) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32, count=dim)
