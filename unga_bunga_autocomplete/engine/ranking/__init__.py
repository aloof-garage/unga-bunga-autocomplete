"""
UNGA BUNGA AUTO-COMPLETE — Ranking Pipeline
============================================
A modular, configurable, explainable ranking system.

Architecture:
    Ranking is a pipeline of scorers, each of which contributes a
    normalised [0.0, 1.0] sub-score.  Sub-scores are combined via a
    weighted sum.  Weights are fully configurable.

    Pipeline stages:
        1. PrefixScorer       — exact prefix match reward
        2. FrequencyScorer    — corpus/training frequency
        3. RecencyScorer      — time-decay of last use
        4. SessionScorer      — in-session selection boost
        5. FuzzyScorer        — DL similarity (from fuzzy engine)
        6. ContextScorer      — n-gram transition probability
        7. NGramScorer        — bigram/trigram co-occurrence

    The final score is the dot product of the normalised sub-scores
    and their weights, then normalised to [0.0, 1.0].

    Explainability: RankedCandidate carries a score_breakdown dict
    so the debug overlay / ranking inspector can show exactly why a
    word ranked where it did.

Thread safety:
    RankingPipeline is stateless during ranking (reads weights, reads
    candidates); thread-safe to call from multiple threads simultaneously.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from ..fuzzy import FuzzyMatch
from ..trie import TrieResult

logger = logging.getLogger(__name__)


# ── Candidate ─────────────────────────────────────────────────────────────────

@dataclass
class RankedCandidate:
    """
    A fully-ranked autocomplete suggestion ready for display.

    Attributes:
        word:            The suggestion string.
        final_score:     Overall ranking score [0.0, 1.0].
        score_breakdown: Sub-score per scorer (for debugging/inspection).
        source:          "trie" | "fuzzy" | "session" | "ngram"
        trie_result:     Original trie result (if available).
        fuzzy_match:     Original fuzzy match (if available).
    """

    word: str
    final_score: float = 0.0
    score_breakdown: Dict[str, float] = field(default_factory=dict)
    source: str = "trie"
    trie_result: Optional[TrieResult] = field(default=None, repr=False)
    fuzzy_match: Optional[FuzzyMatch] = field(default=None, repr=False)

    def explain(self) -> str:
        """Human-readable score explanation."""
        parts = [f"{k}: {v:.3f}" for k, v in sorted(self.score_breakdown.items())]
        return (
            f"'{self.word}' score={self.final_score:.4f} "
            f"source={self.source} [{', '.join(parts)}]"
        )


# ── Weight configuration ──────────────────────────────────────────────────────

@dataclass
class RankingWeights:
    """Configurable weight for each scorer."""
    prefix: float = 3.0
    frequency: float = 2.0
    recency: float = 1.5
    session: float = 2.5
    fuzzy: float = 1.0
    context: float = 1.8
    ngram: float = 1.2

    def total(self) -> float:
        return (
            self.prefix + self.frequency + self.recency
            + self.session + self.fuzzy + self.context + self.ngram
        )

    def normalise_score(self, raw: float) -> float:
        """Normalise a raw weighted sum to [0.0, 1.0]."""
        t = self.total()
        if t <= 0:
            return 0.0
        return min(1.0, max(0.0, raw / t))


# ── Individual scorers ────────────────────────────────────────────────────────

class PrefixScorer:
    """
    Reward exact prefix matches, penalise fuzzier matches.

    Score is 1.0 if the candidate starts exactly with the query prefix,
    linearly degraded by how far into the fuzzy space we've gone.
    """

    @staticmethod
    def score(word: str, prefix: str, is_exact_prefix: bool) -> float:
        """
        Args:
            word:             Candidate word.
            prefix:           User-typed prefix.
            is_exact_prefix:  True if word starts with prefix exactly.

        Returns:
            float [0.0, 1.0]
        """
        if is_exact_prefix:
            # Bonus for short extensions (word is close to the prefix)
            extension_ratio = len(prefix) / max(len(word), 1)
            return 0.5 + 0.5 * extension_ratio
        return 0.0


class FrequencyScorer:
    """
    Log-normalise corpus frequency.

    log(freq + 1) / log(max_freq + 1) → avoids domination by hyper-frequent
    stop words when max_freq is very large.
    """

    def __init__(self, max_frequency: int = 100_000) -> None:
        self._log_max = math.log1p(max(max_frequency, 1))

    def score(self, frequency: int) -> float:
        """
        Args:
            frequency: Raw word frequency.

        Returns:
            float [0.0, 1.0]
        """
        if frequency <= 0:
            return 0.0
        return min(1.0, math.log1p(frequency) / self._log_max)

    def update_max(self, new_max: int) -> None:
        """Update log-normaliser when vocabulary grows."""
        self._log_max = math.log1p(max(new_max, 1))


class RecencyScorer:
    """
    Time-decay scoring.

    Score = 1 / (1 + age_seconds / half_life_seconds)
    With half_life = 3600s (1 hour): recently used words score near 1.0;
    words unused for >6 hours score < 0.15.
    """

    def __init__(self, half_life_s: float = 3600.0) -> None:
        self.half_life_s = half_life_s

    def score(self, recency_timestamp: float) -> float:
        """
        Args:
            recency_timestamp: monotonic timestamp of last use.

        Returns:
            float [0.0, 1.0]
        """
        if recency_timestamp <= 0:
            return 0.0
        age = max(0.0, time.monotonic() - recency_timestamp)
        return 1.0 / (1.0 + age / self.half_life_s)


class SessionScorer:
    """
    In-session selection frequency boost.

    Words the user has selected in the current session get a strong boost.
    This implements the "session learning" feature without any persistence.
    """

    def __init__(self) -> None:
        self._selections: Dict[str, int] = {}

    def record_selection(self, word: str) -> None:
        """Call when user accepts a suggestion."""
        self._selections[word] = self._selections.get(word, 0) + 1

    def score(self, word: str) -> float:
        """
        Score based on in-session selection count.

        Uses log-normalised count capped at 1.0 with saturation at ~10
        selections.

        Returns:
            float [0.0, 1.0]
        """
        count = self._selections.get(word, 0)
        if count <= 0:
            return 0.0
        return min(1.0, math.log1p(count) / math.log1p(10))

    def reset(self) -> None:
        """Clear session data (called on session end)."""
        self._selections.clear()

    @property
    def selection_count(self) -> int:
        return len(self._selections)


class FuzzyScorer:
    """Translate a FuzzyMatch combined score into a ranking sub-score."""

    @staticmethod
    def score(fuzzy_match: Optional[FuzzyMatch]) -> float:
        """
        Returns:
            0.0 if no fuzzy match, else fuzzy_match.combined [0.0, 1.0].
        """
        if fuzzy_match is None:
            return 0.0
        return max(0.0, min(1.0, fuzzy_match.combined))


class ContextScorer:
    """
    Score based on n-gram transition probability.

    Given the last N tokens typed (context), rewards candidates that
    frequently follow those tokens in the training corpus.

    Uses a simple bigram model: P(word | prev_word).
    Transitions stored as: transitions[prev_word][word] = count
    """

    def __init__(self) -> None:
        # bigram: previous_word → {next_word: count}
        self._transitions: Dict[str, Dict[str, int]] = {}
        self._totals: Dict[str, int] = {}  # prev_word → total count

    def record_transition(self, prev_word: str, next_word: str) -> None:
        """Record a word transition observed in training corpus."""
        bucket = self._transitions.setdefault(prev_word, {})
        bucket[next_word] = bucket.get(next_word, 0) + 1
        self._totals[prev_word] = self._totals.get(prev_word, 0) + 1

    def score(self, candidate: str, context_tokens: Sequence[str]) -> float:
        """
        Compute P(candidate | last context token) using bigram model.

        Args:
            candidate:      Word to score.
            context_tokens: Recent tokens (last is most relevant).

        Returns:
            float [0.0, 1.0]  (0.0 if no context or no transition data)
        """
        if not context_tokens:
            return 0.0

        prev = context_tokens[-1].lower()
        bucket = self._transitions.get(prev)
        if not bucket:
            return 0.0

        total = self._totals.get(prev, 1)
        count = bucket.get(candidate.lower(), 0)
        # Laplace-smoothed probability
        prob = (count + 1) / (total + len(bucket) + 1)
        # Compress to [0, 1] via log
        return min(1.0, -math.log(max(prob, 1e-9)) / (-math.log(1e-9)) * -1 + 1)

    def merge(self, other: ContextScorer) -> None:
        """Merge another context scorer's data (for incremental training)."""
        for prev, nexts in other._transitions.items():
            bucket = self._transitions.setdefault(prev, {})
            for word, count in nexts.items():
                bucket[word] = bucket.get(word, 0) + count
            self._totals[prev] = self._totals.get(prev, 0) + other._totals.get(prev, 0)


class NGramScorer:
    """
    Character-level n-gram co-occurrence scorer.

    Used for cross-word completion: if the last N chars of the prefix match
    the start of a candidate, boost it.  This helps when the prefix straddles
    a word boundary (e.g. "hellowor" → "helloworld").
    """

    @staticmethod
    def score(candidate: str, prefix: str, n: int = 3) -> float:
        """
        Fraction of candidate's leading n-grams that appear in prefix.

        Args:
            candidate: Word to score.
            prefix:    User-typed prefix.
            n:         N-gram size.

        Returns:
            float [0.0, 1.0]
        """
        if len(candidate) < n or len(prefix) < n:
            return 0.0

        cand_grams = {candidate[i : i + n] for i in range(len(candidate) - n + 1)}
        prefix_grams = {prefix[i : i + n] for i in range(len(prefix) - n + 1)}

        if not cand_grams:
            return 0.0

        overlap = len(cand_grams & prefix_grams)
        return overlap / len(cand_grams)


# ── Pipeline ──────────────────────────────────────────────────────────────────

class RankingPipeline:
    """
    Orchestrates all scorers into a final ranked list.

    Usage::

        pipeline = RankingPipeline()
        results = pipeline.rank(
            prefix="hel",
            trie_results=trie.search_prefix("hel"),
            fuzzy_results=fuzzy.search("hel", vocab),
            context_tokens=["say", "hello"],
        )
        for c in results:
            print(c.explain())

    The pipeline is reentrant and thread-safe.
    """

    def __init__(
        self,
        weights: Optional[RankingWeights] = None,
        max_frequency: int = 100_000,
    ) -> None:
        self.weights = weights or RankingWeights()
        self.freq_scorer = FrequencyScorer(max_frequency)
        self.recency_scorer = RecencyScorer()
        self.session_scorer = SessionScorer()
        self.context_scorer = ContextScorer()
        logger.debug("RankingPipeline initialised with weights: %s", self.weights)

    def rank(
        self,
        prefix: str,
        trie_results: List[TrieResult],
        fuzzy_results: Optional[List[FuzzyMatch]] = None,
        context_tokens: Optional[Sequence[str]] = None,
        max_results: int = 10,
    ) -> List[RankedCandidate]:
        """
        Rank all candidates and return the top *max_results*.

        Steps:
        1. Merge trie + fuzzy results into unified candidate pool
        2. Score each candidate through all scorers
        3. Compute weighted sum
        4. Sort descending, return top-k

        Args:
            prefix:          User-typed prefix.
            trie_results:    Results from Trie.search_prefix.
            fuzzy_results:   Results from FuzzyEngine.search (optional).
            context_tokens:  Previous words for context scoring.
            max_results:     How many to return.

        Returns:
            List of RankedCandidate, best first.
        """
        ctx = context_tokens or []
        fuzzy_results = fuzzy_results or []
        fuzzy_map = {fm.word: fm for fm in fuzzy_results}

        # Build candidate pool: trie results first, then fuzzy-only
        candidates: List[RankedCandidate] = []
        seen_words: set = set()

        for tr in trie_results:
            if tr.word in seen_words:
                continue
            seen_words.add(tr.word)
            candidates.append(
                RankedCandidate(
                    word=tr.word,
                    source="trie",
                    trie_result=tr,
                    fuzzy_match=fuzzy_map.get(tr.word),
                )
            )

        for fm in fuzzy_results:
            if fm.word in seen_words:
                continue
            seen_words.add(fm.word)
            candidates.append(
                RankedCandidate(
                    word=fm.word,
                    source="fuzzy",
                    fuzzy_match=fm,
                )
            )

        # Score each candidate
        for cand in candidates:
            self._score_candidate(cand, prefix, ctx)

        # Sort descending
        candidates.sort(key=lambda c: c.final_score, reverse=True)
        return candidates[:max_results]

    def _score_candidate(
        self,
        cand: RankedCandidate,
        prefix: str,
        context_tokens: Sequence[str],
    ) -> None:
        """Compute and attach scores to *cand* in-place."""
        w = self.weights
        tr = cand.trie_result
        fm = cand.fuzzy_match

        is_exact_prefix = cand.word.lower().startswith(prefix.lower())

        s_prefix = PrefixScorer.score(cand.word, prefix, is_exact_prefix)
        s_freq = self.freq_scorer.score(tr.frequency if tr else 0)
        s_recency = self.recency_scorer.score(tr.recency if tr else 0.0)
        s_session = self.session_scorer.score(cand.word)
        s_fuzzy = FuzzyScorer.score(fm)
        s_context = self.context_scorer.score(cand.word, context_tokens)
        s_ngram = NGramScorer.score(cand.word, prefix)

        raw = (
            w.prefix * s_prefix
            + w.frequency * s_freq
            + w.recency * s_recency
            + w.session * s_session
            + w.fuzzy * s_fuzzy
            + w.context * s_context
            + w.ngram * s_ngram
        )

        cand.final_score = w.normalise_score(raw)
        cand.score_breakdown = {
            "prefix": round(s_prefix, 4),
            "frequency": round(s_freq, 4),
            "recency": round(s_recency, 4),
            "session": round(s_session, 4),
            "fuzzy": round(s_fuzzy, 4),
            "context": round(s_context, 4),
            "ngram": round(s_ngram, 4),
        }

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def record_selection(self, word: str, context_tokens: Sequence[str]) -> None:
        """
        Called when the user accepts a suggestion.

        Updates session scorer and context transitions.

        Args:
            word:           Accepted word.
            context_tokens: Tokens before the accepted word.
        """
        self.session_scorer.record_selection(word)
        if context_tokens:
            self.context_scorer.record_transition(context_tokens[-1], word)
        logger.debug("Selection recorded: %r (context=%r)", word, context_tokens)

    def update_max_frequency(self, max_freq: int) -> None:
        """Update frequency normaliser after large training pass."""
        self.freq_scorer.update_max(max_freq)

    def reset_session(self) -> None:
        """Clear session state (call on new document / session start)."""
        self.session_scorer.reset()
        logger.debug("Session scorer reset")
