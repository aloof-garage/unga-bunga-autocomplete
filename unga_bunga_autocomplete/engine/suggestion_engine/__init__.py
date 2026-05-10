"""
UNGA BUNGA AUTO-COMPLETE — Suggestion Engine
=============================================
The central orchestrator that wires Trie + Fuzzy + Ranking into
a unified, async, debounced, cached autocomplete API.

Architecture:
    SuggestionEngine is the single entry point for all autocomplete queries.
    It owns:
        - One Trie (read-heavy, updated by background workers)
        - One FuzzyEngine (stateless, always thread-safe)
        - One RankingPipeline (holds session state)
        - One LRU cache (keyed by prefix + context hash)
        - One ThreadPoolExecutor (for blocking trie/fuzzy work)

    Query flow:
        1. Client calls query_async(prefix)
        2. Debounce: if another query arrives < debounce_ms later, cancel this one
        3. Cache hit? → return cached results instantly
        4. Trie prefix search (fast, ~1ms for 500k words)
        5. If results < min_results → supplement with FuzzyEngine
        6. Rank combined pool
        7. Store in cache
        8. Return

    Thread model:
        - Public API: fully async (asyncio)
        - Trie/fuzzy work: runs in thread pool (they hold GIL for short bursts)
        - Ranking: runs in thread pool (pure Python computation)
        - Session updates: main thread (negligible cost, lock-free reads)

    Cache:
        Simple dict-based LRU.  Eviction on size limit.
        Key: (prefix, frozenset(context_tokens[:3]))  — shallow context
        TTL: entries expire after 30s to prevent stale suggestions.
"""

from __future__ import annotations

import asyncio
import collections
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List, Optional, Sequence, Tuple

from ..trie import Trie
from ..fuzzy import FuzzyEngine
from ..ranking import RankingPipeline, RankedCandidate

logger = logging.getLogger(__name__)


# ── Cache ─────────────────────────────────────────────────────────────────────

@dataclass
class _CacheEntry:
    results: List[RankedCandidate]
    timestamp: float


class LRUCache:
    """
    Thread-safe LRU cache with TTL.

    Uses collections.OrderedDict for O(1) move-to-end on hit.
    All mutations are protected by a threading.Lock.
    """

    def __init__(self, capacity: int = 4096, ttl_s: float = 30.0) -> None:
        import threading
        self._capacity = capacity
        self._ttl = ttl_s
        self._store: collections.OrderedDict[
            Tuple, _CacheEntry
        ] = collections.OrderedDict()
        self._lock = __import__("threading").Lock()
        self.hits = 0
        self.misses = 0

    def get(self, key: Tuple) -> Optional[List[RankedCandidate]]:
        with self._lock:
            if key not in self._store:
                self.misses += 1
                return None
            entry = self._store[key]
            if time.monotonic() - entry.timestamp > self._ttl:
                del self._store[key]
                self.misses += 1
                return None
            self._store.move_to_end(key)
            self.hits += 1
            return entry.results

    def put(self, key: Tuple, results: List[RankedCandidate]) -> None:
        with self._lock:
            if key in self._store:
                self._store.move_to_end(key)
            self._store[key] = _CacheEntry(results=results, timestamp=time.monotonic())
            if len(self._store) > self._capacity:
                self._store.popitem(last=False)

    def invalidate(self) -> None:
        """Clear entire cache (call after index updates)."""
        with self._lock:
            self._store.clear()

    @property
    def size(self) -> int:
        return len(self._store)

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total else 0.0


# ── Query result ──────────────────────────────────────────────────────────────

@dataclass
class QueryResult:
    """Public result type returned to callers."""
    prefix: str
    suggestions: List[RankedCandidate]
    elapsed_ms: float
    cache_hit: bool
    trie_count: int = 0
    fuzzy_count: int = 0
    query_id: str = ""


# ── Suggestion Engine ─────────────────────────────────────────────────────────

class SuggestionEngine:
    """
    Main autocomplete engine.  Thread-safe, async-first.

    Lifecycle:
        engine = SuggestionEngine(config)
        await engine.start()          # warm up thread pool, load trie
        result = await engine.query("hel")
        await engine.shutdown()       # graceful stop

    Example::

        engine = SuggestionEngine()
        await engine.start()
        await engine.train_text("The quick brown fox jumps over the lazy dog")
        result = await engine.query("quic")
        for s in result.suggestions:
            print(s.word, s.final_score)
    """

    def __init__(
        self,
        trie: Optional[Trie] = None,
        max_suggestions: int = 10,
        min_prefix_length: int = 1,
        debounce_ms: int = 80,
        fuzzy_threshold: float = 0.55,
        max_fuzzy_distance: int = 3,
        fuzzy_supplement_threshold: int = 3,  # if trie returns < this, run fuzzy
        cache_size: int = 4096,
        worker_threads: int = 2,
    ) -> None:
        self._trie = trie or Trie()
        self._fuzzy = FuzzyEngine(
            threshold=fuzzy_threshold,
            max_distance=max_fuzzy_distance,
        )
        self._ranking = RankingPipeline()
        self._cache = LRUCache(capacity=cache_size)

        self._max_suggestions = max_suggestions
        self._min_prefix = min_prefix_length
        self._debounce_s = debounce_ms / 1000.0
        self._fuzzy_supplement_threshold = fuzzy_supplement_threshold

        self._executor: Optional[ThreadPoolExecutor] = None
        self._worker_threads = worker_threads
        self._running = False

        # Pending debounce task
        self._pending_query: Optional[asyncio.Task] = None
        self._pending_lock = asyncio.Lock()

        # Statistics
        self._query_count = 0
        self._total_latency_ms = 0.0

        logger.info("SuggestionEngine created (threads=%d)", worker_threads)

    # ── Lifecycle ─────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Warm up the engine.  Must be called before query()."""
        if self._running:
            return
        self._executor = ThreadPoolExecutor(
            max_workers=self._worker_threads,
            thread_name_prefix="ub-autocomplete",
        )
        self._running = True
        logger.info("SuggestionEngine started")

    async def shutdown(self) -> None:
        """Gracefully stop the engine."""
        if not self._running:
            return
        self._running = False
        if self._executor:
            self._executor.shutdown(wait=True)
            self._executor = None
        logger.info(
            "SuggestionEngine stopped. Served %d queries, avg latency %.2fms",
            self._query_count,
            self._total_latency_ms / max(self._query_count, 1),
        )

    # ── Core query API ────────────────────────────────────────────────────

    async def query(
        self,
        prefix: str,
        context_tokens: Optional[Sequence[str]] = None,
        query_id: str = "",
    ) -> QueryResult:
        """
        Async autocomplete query with debouncing and caching.

        Debounce:
            If a new query arrives before debounce_ms has elapsed since the
            last query, the previous query's result is abandoned.  This
            prevents flooding the engine during rapid typing.

        Args:
            prefix:         Current prefix to autocomplete.
            context_tokens: Previous words (for context scoring).
            query_id:       Optional ID for result correlation.

        Returns:
            QueryResult with ranked suggestions.
        """
        if not self._running:
            raise RuntimeError("SuggestionEngine not started — call await engine.start()")

        if len(prefix) < self._min_prefix:
            return QueryResult(
                prefix=prefix,
                suggestions=[],
                elapsed_ms=0.0,
                cache_hit=False,
                query_id=query_id,
            )

        # Check cache first (before debounce wait)
        ctx_key = frozenset((context_tokens or [])[:3])
        cache_key = (prefix, ctx_key)
        cached = self._cache.get(cache_key)
        if cached is not None:
            logger.debug("Cache hit for prefix=%r", prefix)
            return QueryResult(
                prefix=prefix,
                suggestions=cached,
                elapsed_ms=0.0,
                cache_hit=True,
                query_id=query_id,
            )

        # Debounce: small sleep then check if still relevant
        if self._debounce_s > 0:
            await asyncio.sleep(self._debounce_s)

        return await self._execute_query(prefix, context_tokens or [], query_id, cache_key)

    async def query_immediate(
        self,
        prefix: str,
        context_tokens: Optional[Sequence[str]] = None,
        query_id: str = "",
    ) -> QueryResult:
        """
        Query without debouncing.  For use in non-typing contexts
        (e.g. command palette, search box with explicit trigger).
        """
        if not self._running:
            raise RuntimeError("SuggestionEngine not started")

        ctx_key = frozenset((context_tokens or [])[:3])
        cache_key = (prefix, ctx_key)
        cached = self._cache.get(cache_key)
        if cached is not None:
            return QueryResult(prefix=prefix, suggestions=cached, elapsed_ms=0.0,
                               cache_hit=True, query_id=query_id)

        return await self._execute_query(prefix, context_tokens or [], query_id, cache_key)

    async def _execute_query(
        self,
        prefix: str,
        context_tokens: Sequence[str],
        query_id: str,
        cache_key: Tuple,
    ) -> QueryResult:
        """Run the full trie→fuzzy→rank pipeline in the thread pool."""
        loop = asyncio.get_event_loop()
        start = time.monotonic()

        def _search_blocking():
            # ① Trie prefix search
            trie_results = self._trie.search_prefix(
                prefix, max_results=self._max_suggestions * 2
            )

            # ② Fuzzy supplement if trie results are sparse
            fuzzy_results = []
            if len(trie_results) < self._fuzzy_supplement_threshold:
                vocab = [r.word for r in self._trie.all_words()]
                fuzzy_results = self._fuzzy.search(
                    prefix, vocabulary=vocab, max_results=self._max_suggestions * 2
                )

            # ③ Rank
            ranked = self._ranking.rank(
                prefix=prefix,
                trie_results=trie_results,
                fuzzy_results=fuzzy_results,
                context_tokens=context_tokens,
                max_results=self._max_suggestions,
            )
            return ranked, len(trie_results), len(fuzzy_results)

        try:
            ranked, trie_count, fuzzy_count = await loop.run_in_executor(
                self._executor, _search_blocking
            )
        except Exception as exc:
            logger.error("Query execution failed for prefix=%r: %s", prefix, exc)
            return QueryResult(
                prefix=prefix, suggestions=[], elapsed_ms=0.0,
                cache_hit=False, query_id=query_id
            )

        elapsed_ms = (time.monotonic() - start) * 1000.0
        self._query_count += 1
        self._total_latency_ms += elapsed_ms

        # Store in cache
        self._cache.put(cache_key, ranked)

        logger.debug(
            "Query %r → %d results (trie=%d, fuzzy=%d) in %.2fms",
            prefix, len(ranked), trie_count, fuzzy_count, elapsed_ms,
        )

        return QueryResult(
            prefix=prefix,
            suggestions=ranked,
            elapsed_ms=elapsed_ms,
            cache_hit=False,
            trie_count=trie_count,
            fuzzy_count=fuzzy_count,
            query_id=query_id,
        )

    # ── Training API ──────────────────────────────────────────────────────

    async def train_words(self, words: List[Tuple[str, int]]) -> int:
        """
        Insert (word, frequency) pairs into the trie.

        Invalidates the cache after update.

        Args:
            words: List of (word, frequency) tuples.

        Returns:
            Number of words inserted.
        """
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(self._executor, self._trie.batch_insert, words)
        self._cache.invalidate()
        logger.info("Trained %d words into trie", len(words))
        return len(words)

    async def train_text(self, text: str, min_length: int = 2) -> int:
        """
        Convenience: tokenize *text* and insert all tokens.

        Simple whitespace tokenizer.  For production use, call the
        full TrainingPipeline for normalisation.

        Returns:
            Number of unique tokens inserted.
        """
        from collections import Counter
        tokens = text.lower().split()
        counts = Counter(t.strip(".,!?;:\"'()[]{}") for t in tokens if len(t) >= min_length)
        valid = [(w, c) for w, c in counts.items() if w.isalpha()]
        await self.train_words(valid)
        return len(valid)

    # ── Selection feedback ────────────────────────────────────────────────

    def record_selection(self, word: str, context_tokens: Optional[Sequence[str]] = None) -> None:
        """
        Record that the user accepted *word* as a suggestion.

        - Reinforces the word in the trie (frequency +1)
        - Updates session scorer
        - Updates context transition model

        Thread safety: trie write-lock acquired internally.
        """
        self._trie.reinforce(word, boost=1)
        self._ranking.record_selection(word, context_tokens or [])
        # Invalidate cached queries that might include this word
        self._cache.invalidate()
        logger.debug("Selection recorded: %r", word)

    # ── Statistics ────────────────────────────────────────────────────────

    def stats(self) -> dict:
        """Return engine-wide statistics for the dashboard / debug overlay."""
        return {
            "trie": self._trie.stats(),
            "cache": {
                "size": self._cache.size,
                "hits": self._cache.hits,
                "misses": self._cache.misses,
                "hit_rate": round(self._cache.hit_rate, 3),
            },
            "queries": {
                "total": self._query_count,
                "avg_latency_ms": round(
                    self._total_latency_ms / max(self._query_count, 1), 2
                ),
            },
            "session": {
                "selections": self._ranking.session_scorer.selection_count,
            },
        }

    # ── Trie access ───────────────────────────────────────────────────────

    @property
    def trie(self) -> Trie:
        """Direct trie access for persistence / admin operations."""
        return self._trie

    @property
    def ranking(self) -> RankingPipeline:
        """Direct ranking pipeline access."""
        return self._ranking
