"""
UNGA BUNGA AUTO-COMPLETE — Training Pipeline
============================================
Corpus ingestion → normalisation → tokenisation → frequency analysis →
n-gram extraction → trie population.

Stages:
    1. Ingest: read text from files, strings, or iterators
    2. Normalise: Unicode normalisation, case-folding, punctuation stripping
    3. Tokenise: split into meaningful tokens
    4. Frequency: count occurrences
    5. N-gram: build bigram/trigram transition tables
    6. Persist: save frequencies to SQLite + trie snapshot

Incremental training:
    Each pass updates frequencies additively.  The engine supports
    live retraining without restart via batch_insert.

Thread safety:
    Each TrainingPipeline instance is NOT thread-safe internally.
    Use one per worker thread.  Results are merged via merge().
"""

from __future__ import annotations

import logging
import re
import time
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Generator, Iterable, Iterator, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ── Configuration ─────────────────────────────────────────────────────────────

@dataclass
class TokeniserConfig:
    """Tokenisation behaviour settings."""
    lowercase: bool = True
    normalize_unicode: bool = True           # NFC normalisation
    strip_punctuation: bool = True
    strip_numbers: bool = False              # keep numbers by default
    min_token_length: int = 2
    max_token_length: int = 64
    split_on_camel_case: bool = True         # "CamelCase" → ["Camel", "Case"]
    split_on_underscores: bool = True        # "foo_bar" → ["foo", "bar"]
    ngram_sizes: List[int] = field(default_factory=lambda: [2, 3])
    max_vocab_size: int = 500_000


# ── Tokeniser ─────────────────────────────────────────────────────────────────

_PUNCT_RE = re.compile(r"[^\w\s'-]", re.UNICODE)
_WHITESPACE_RE = re.compile(r"\s+")
_CAMEL_RE = re.compile(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_NUMBER_RE = re.compile(r"^\d+$")


class Tokeniser:
    """
    Text tokeniser that produces clean, normalised tokens.

    Unicode:
        All input is NFC-normalised (composed form) before processing.
        This ensures "café" and "cafe\u0301" produce identical tokens.

    Camel case splitting:
        "helloWorld" → ["hello", "World"] → ["hello", "world"]
        Useful for code completion corpora.

    Args:
        config: TokeniserConfig instance.
    """

    def __init__(self, config: Optional[TokeniserConfig] = None) -> None:
        self.config = config or TokeniserConfig()

    def tokenise(self, text: str) -> List[str]:
        """
        Tokenise *text* into a list of clean tokens.

        Args:
            text: Raw input text.

        Returns:
            List of normalised tokens (may be empty).
        """
        if not text:
            return []

        cfg = self.config

        # Unicode normalisation
        if cfg.normalize_unicode:
            text = unicodedata.normalize("NFC", text)

        # Camel case split (before lowercasing)
        if cfg.split_on_camel_case:
            text = _CAMEL_RE.sub(" ", text)

        # Underscore split
        if cfg.split_on_underscores:
            text = text.replace("_", " ")

        # Lowercase
        if cfg.lowercase:
            text = text.lower()

        # Strip punctuation (preserve apostrophes and hyphens for contractions)
        if cfg.strip_punctuation:
            text = _PUNCT_RE.sub(" ", text)

        # Split on whitespace
        raw_tokens = _WHITESPACE_RE.split(text)

        # Filter
        tokens: List[str] = []
        for tok in raw_tokens:
            tok = tok.strip("'-")  # clean leading/trailing apostrophes/hyphens
            if not tok:
                continue
            if len(tok) < cfg.min_token_length:
                continue
            if len(tok) > cfg.max_token_length:
                tok = tok[: cfg.max_token_length]
            if cfg.strip_numbers and _NUMBER_RE.match(tok):
                continue
            tokens.append(tok)

        return tokens

    def tokenise_stream(self, lines: Iterable[str]) -> Generator[str, None, None]:
        """Tokenise an iterable of lines, yielding tokens one at a time."""
        for line in lines:
            yield from self.tokenise(line)


# ── Frequency builder ─────────────────────────────────────────────────────────

class FrequencyBuilder:
    """
    Accumulates token frequencies and n-gram co-occurrence counts.

    Frequency storage:
        frequencies: Counter[str] — total occurrence count per token
        bigrams:     Counter[Tuple[str,str]] — ordered bigram counts
        trigrams:    Counter[Tuple[str,str,str]] — ordered trigram counts

    Memory budget:
        For 500k unique tokens × (8 bytes key + 8 bytes count) ≈ ~8MB.
        Bigrams at 10× vocabulary ≈ ~80MB.  Bounded by max_vocab_size.
    """

    def __init__(self, config: Optional[TokeniserConfig] = None) -> None:
        self.config = config or TokeniserConfig()
        self.frequencies: Counter = Counter()
        self.bigrams: Counter = Counter()
        self.trigrams: Counter = Counter()
        self._total_tokens: int = 0
        self._documents: int = 0

    def feed(self, tokens: List[str]) -> None:
        """
        Feed a token sequence into the builder.

        Updates frequencies and n-gram counts.

        Args:
            tokens: Pre-tokenised, normalised token list.
        """
        if not tokens:
            return

        self._documents += 1
        self._total_tokens += len(tokens)

        # Frequency
        self.frequencies.update(tokens)

        # Enforce vocab limit
        if len(self.frequencies) > self.config.max_vocab_size:
            # Keep only most frequent words (prune rare ones)
            self.frequencies = Counter(
                dict(self.frequencies.most_common(self.config.max_vocab_size))
            )

        # Bigrams
        if 2 in self.config.ngram_sizes:
            for i in range(len(tokens) - 1):
                self.bigrams[(tokens[i], tokens[i + 1])] += 1

        # Trigrams
        if 3 in self.config.ngram_sizes:
            for i in range(len(tokens) - 2):
                self.trigrams[(tokens[i], tokens[i + 1], tokens[i + 2])] += 1

    def feed_text(self, text: str, tokeniser: Optional[Tokeniser] = None) -> int:
        """
        Tokenise and feed *text*.

        Args:
            text:      Raw text to process.
            tokeniser: Optional Tokeniser; creates one with default config if None.

        Returns:
            Number of tokens processed.
        """
        tok = tokeniser or Tokeniser(self.config)
        tokens = tok.tokenise(text)
        self.feed(tokens)
        return len(tokens)

    def feed_file(self, path: Path, tokeniser: Optional[Tokeniser] = None) -> int:
        """
        Read and feed a text file line-by-line.

        Handles large files without loading into memory.

        Args:
            path:      Path to text file (UTF-8 or Latin-1 fallback).
            tokeniser: Optional Tokeniser.

        Returns:
            Total tokens processed.

        Raises:
            OSError: If file cannot be read.
        """
        tok = tokeniser or Tokeniser(self.config)
        total = 0

        try:
            with path.open("r", encoding="utf-8", errors="replace") as fh:
                batch: List[str] = []
                for line in fh:
                    tokens = tok.tokenise(line)
                    batch.extend(tokens)
                    if len(batch) >= 10_000:
                        self.feed(batch)
                        total += len(batch)
                        batch.clear()
                if batch:
                    self.feed(batch)
                    total += len(batch)
        except OSError as exc:
            logger.error("Failed to read corpus file %s: %s", path, exc)
            raise

        logger.info("Processed %d tokens from %s", total, path)
        return total

    def merge(self, other: FrequencyBuilder) -> None:
        """
        Merge *other* builder into this one.

        Used to aggregate results from parallel workers.

        Thread safety: NOT thread-safe.  Call from a single coordinator thread.
        """
        self.frequencies.update(other.frequencies)
        self.bigrams.update(other.bigrams)
        self.trigrams.update(other.trigrams)
        self._total_tokens += other._total_tokens
        self._documents += other._documents

    def top_words(self, n: int = 10_000) -> List[Tuple[str, int]]:
        """Return the top-n most frequent words."""
        return self.frequencies.most_common(n)

    def to_trie_batch(self, limit: Optional[int] = None) -> List[Tuple[str, int]]:
        """
        Return (word, frequency) pairs ready for Trie.batch_insert.

        Args:
            limit: Optional max number of words (by frequency).

        Returns:
            List of (word, frequency) tuples.
        """
        words = self.frequencies.most_common(limit)
        return [(w, c) for w, c in words if c > 0]

    def bigram_transitions(self) -> Dict[str, Dict[str, int]]:
        """
        Convert bigram counter to nested dict for ContextScorer.

        Returns:
            Dict mapping prev_word → {next_word: count}
        """
        result: Dict[str, Dict[str, int]] = defaultdict(dict)
        for (prev, nxt), count in self.bigrams.items():
            result[prev][nxt] = count
        return dict(result)

    def stats(self) -> dict:
        """Return training statistics."""
        return {
            "vocab_size": len(self.frequencies),
            "total_tokens": self._total_tokens,
            "documents": self._documents,
            "unique_bigrams": len(self.bigrams),
            "unique_trigrams": len(self.trigrams),
            "most_common_10": [w for w, _ in self.frequencies.most_common(10)],
        }


# ── Training pipeline orchestrator ────────────────────────────────────────────

@dataclass
class TrainingResult:
    """Result from a training pass."""
    corpus_name: str
    token_count: int
    vocab_size: int
    elapsed_s: float
    words_in_trie: int
    warnings: List[str] = field(default_factory=list)


class TrainingPipeline:
    """
    End-to-end training orchestrator.

    Owns a FrequencyBuilder and drives the full ingestion → trie update cycle.

    Usage::

        pipeline = TrainingPipeline()
        await pipeline.train_file(Path("corpus.txt"), engine)
        await pipeline.train_text("hello world hello", engine)
    """

    def __init__(self, config: Optional[TokeniserConfig] = None) -> None:
        self.config = config or TokeniserConfig()
        self._tokeniser = Tokeniser(self.config)
        self._builder = FrequencyBuilder(self.config)
        logger.debug("TrainingPipeline initialised")

    async def train_text(
        self,
        text: str,
        engine,  # SuggestionEngine — avoid circular import with forward ref
        corpus_name: str = "inline",
    ) -> TrainingResult:
        """
        Train on raw text string.

        Args:
            text:        Raw text to learn from.
            engine:      SuggestionEngine to update.
            corpus_name: Label for logging.

        Returns:
            TrainingResult with statistics.
        """
        start = time.monotonic()
        warnings: List[str] = []

        try:
            token_count = self._builder.feed_text(text, self._tokeniser)
        except Exception as exc:
            warnings.append(f"Tokenisation error: {exc}")
            token_count = 0

        # Push to trie
        batch = self._builder.to_trie_batch()
        words_inserted = await engine.train_words(batch)

        # Update context scorer with bigram transitions
        transitions = self._builder.bigram_transitions()
        for prev, nexts in transitions.items():
            for nxt, count in nexts.items():
                for _ in range(min(count, 5)):  # cap to avoid OOM
                    engine.ranking.context_scorer.record_transition(prev, nxt)

        elapsed = time.monotonic() - start
        result = TrainingResult(
            corpus_name=corpus_name,
            token_count=token_count,
            vocab_size=len(self._builder.frequencies),
            elapsed_s=elapsed,
            words_in_trie=words_inserted,
            warnings=warnings,
        )
        logger.info("Training complete: %s", result)
        return result

    async def train_file(
        self,
        path: Path,
        engine,
        corpus_name: Optional[str] = None,
    ) -> TrainingResult:
        """
        Train from a text file.

        Args:
            path:        Path to .txt corpus file.
            engine:      SuggestionEngine to update.
            corpus_name: Label; defaults to filename.

        Returns:
            TrainingResult.
        """
        import asyncio
        name = corpus_name or path.name
        start = time.monotonic()
        warnings: List[str] = []

        loop = asyncio.get_event_loop()
        try:
            token_count = await loop.run_in_executor(
                None, self._builder.feed_file, path, self._tokeniser
            )
        except OSError as exc:
            warnings.append(f"File read error: {exc}")
            return TrainingResult(
                corpus_name=name, token_count=0, vocab_size=0,
                elapsed_s=0.0, words_in_trie=0, warnings=warnings,
            )

        batch = self._builder.to_trie_batch()
        words_inserted = await engine.train_words(batch)

        elapsed = time.monotonic() - start
        return TrainingResult(
            corpus_name=name,
            token_count=token_count,
            vocab_size=len(self._builder.frequencies),
            elapsed_s=elapsed,
            words_in_trie=words_inserted,
            warnings=warnings,
        )

    @property
    def stats(self) -> dict:
        return self._builder.stats()
