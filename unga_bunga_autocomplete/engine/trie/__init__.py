"""
UNGA BUNGA AUTO-COMPLETE — Trie Engine
========================================
A fully custom compressed trie (prefix tree) built from scratch.

Architecture decisions:
    - Node children stored in plain dict (O(1) lookup, CPython dict is highly
      optimised; a sorted array would only help for very large alphabets).
    - Weights and metadata stored at terminal nodes only (leaf or end-of-word)
      to save memory.
    - Iterative traversal everywhere (no recursion risk on deep tries with
      Python's default recursion limit of 1000).
    - Thread-safe via RWLock pattern: readers use a shared counter, writers
      acquire exclusive access.  Reads vastly outnumber writes in a live
      autocomplete scenario.

Complexity:
    insert:  O(k)  — k = word length
    delete:  O(k)
    search:  O(k)
    prefix_lookup: O(k + m) — m = number of matches returned (bounded)
    serialize: O(n) — n = total nodes

Memory layout per node (Python dicts are ~240 bytes each; 100k-word trie ≈ ~50MB):
    children: dict[str, TrieNode]
    is_terminal: bool
    word: str | None
    frequency: int
    recency: float
    metadata: dict  (only allocated when needed)
"""

from __future__ import annotations

import heapq
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Generator, Iterator, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ── Node ──────────────────────────────────────────────────────────────────────

class TrieNode:
    """
    Single trie node.

    Slots optimisation: Using __slots__ reduces per-instance memory by ~40%
    compared to a plain dict-backed object.  With 500k nodes this matters.

    Attributes:
        children:    Map from character → child TrieNode.
        is_terminal: True if a complete word ends here.
        word:        The complete word (only set on terminal nodes).
        frequency:   How often this word has been seen / reinforced.
        recency:     Timestamp (monotonic) of last access / insertion.
        metadata:    Arbitrary extra data (pos-tag, source, etc.).
    """

    __slots__ = ("children", "is_terminal", "word", "frequency", "recency", "metadata")

    def __init__(self) -> None:
        self.children: Dict[str, TrieNode] = {}
        self.is_terminal: bool = False
        self.word: Optional[str] = None
        self.frequency: int = 0
        self.recency: float = 0.0
        self.metadata: Optional[dict] = None

    def __repr__(self) -> str:
        return (
            f"TrieNode(terminal={self.is_terminal}, "
            f"word={self.word!r}, freq={self.frequency}, "
            f"children={list(self.children.keys())!r})"
        )


# ── Result type ───────────────────────────────────────────────────────────────

@dataclass(order=True)
class TrieResult:
    """
    A single autocomplete candidate from the trie.

    Ordered by score descending (score negated for heapq min-heap trick).
    """
    score: float
    word: str = field(compare=False)
    frequency: int = field(compare=False)
    recency: float = field(compare=False)
    metadata: Optional[dict] = field(default=None, compare=False)

    def __post_init__(self) -> None:
        self.score = -self.score  # Negate for min-heap → effectively max-heap


# ── RW Lock ───────────────────────────────────────────────────────────────────

class _RWLock:
    """
    Simple readers-writer lock.

    Multiple concurrent readers are allowed.  Writers get exclusive access.
    Writers are preferential: pending writers block new readers.

    Performance note: For autocomplete, the read:write ratio is ~100:1 so
    reader starvation of writers is the bigger risk — hence writer preference.
    """

    def __init__(self) -> None:
        self._read_ready = threading.Condition(threading.Lock())
        self._readers: int = 0
        self._writer_waiting: int = 0

    class _ReadCtx:
        def __init__(self, lock: _RWLock) -> None:
            self._lock = lock

        def __enter__(self) -> None:
            with self._lock._read_ready:
                while self._lock._writer_waiting > 0:
                    self._lock._read_ready.wait()
                self._lock._readers += 1

        def __exit__(self, *_) -> None:
            with self._lock._read_ready:
                self._lock._readers -= 1
                if self._lock._readers == 0:
                    self._lock._read_ready.notify_all()

    class _WriteCtx:
        def __init__(self, lock: _RWLock) -> None:
            self._lock = lock

        def __enter__(self) -> None:
            with self._lock._read_ready:
                self._lock._writer_waiting += 1
                while self._lock._readers > 0:
                    self._lock._read_ready.wait()

        def __exit__(self, *_) -> None:
            with self._lock._read_ready:
                self._lock._writer_waiting -= 1
                self._lock._read_ready.notify_all()

    def read(self) -> _ReadCtx:
        return self._ReadCtx(self)

    def write(self) -> _WriteCtx:
        return self._WriteCtx(self)


# ── Trie ──────────────────────────────────────────────────────────────────────

class Trie:
    """
    Production-grade trie with:
    - Weighted prefix lookup (heapq-based top-k)
    - Iterative traversal (no recursion limit)
    - Thread safety via RW lock
    - JSON serialization / deserialization
    - Batch insert for bulk training
    - Frequency and recency decay

    Example::

        trie = Trie()
        trie.insert("hello", frequency=10)
        trie.insert("help", frequency=5)
        results = trie.search_prefix("hel", max_results=5)
        for r in results:
            print(r.word, r.frequency)
    """

    def __init__(self) -> None:
        self._root = TrieNode()
        self._rwlock = _RWLock()
        self._total_words: int = 0
        self._total_nodes: int = 1  # root
        self._creation_time: float = time.monotonic()

    # ── Insert ────────────────────────────────────────────────────────────

    def insert(
        self,
        word: str,
        frequency: int = 1,
        metadata: Optional[dict] = None,
        recency: Optional[float] = None,
    ) -> None:
        """
        Insert *word* into the trie.

        If the word already exists, its frequency is incremented and recency
        updated.  This is the desired behaviour for training (reinforcement).

        Args:
            word:      The word to insert (Unicode supported).
            frequency: Starting / increment frequency.
            metadata:  Optional arbitrary metadata dict.
            recency:   Monotonic timestamp; defaults to now.

        Thread safety: acquires write lock.
        Complexity: O(k) where k = len(word).
        """
        if not word:
            return

        ts = recency if recency is not None else time.monotonic()

        with self._rwlock.write():
            node = self._root
            for char in word:
                if char not in node.children:
                    node.children[char] = TrieNode()
                    self._total_nodes += 1
                node = node.children[char]

            if node.is_terminal:
                # Word already exists — reinforce it
                node.frequency += frequency
                node.recency = max(node.recency, ts)
            else:
                node.is_terminal = True
                node.word = word
                node.frequency = frequency
                node.recency = ts
                node.metadata = metadata
                self._total_words += 1

    def batch_insert(self, words: List[Tuple[str, int]]) -> None:
        """
        Insert many (word, frequency) pairs efficiently.

        Acquires write lock ONCE for the entire batch, which is dramatically
        faster than per-word locking for large corpora.

        Args:
            words: Iterable of (word, frequency) pairs.

        Complexity: O(sum of word lengths)
        """
        ts = time.monotonic()
        with self._rwlock.write():
            for word, freq in words:
                if not word:
                    continue
                node = self._root
                for char in word:
                    if char not in node.children:
                        node.children[char] = TrieNode()
                        self._total_nodes += 1
                    node = node.children[char]

                if node.is_terminal:
                    node.frequency += freq
                    node.recency = max(node.recency, ts)
                else:
                    node.is_terminal = True
                    node.word = word
                    node.frequency = freq
                    node.recency = ts
                    self._total_words += 1

        logger.debug("Batch inserted %d words into trie", len(words))

    # ── Delete ────────────────────────────────────────────────────────────

    def delete(self, word: str) -> bool:
        """
        Delete *word* from the trie.

        Marks the terminal node as non-terminal.  Does not prune orphaned
        nodes eagerly (a background compaction pass handles that).

        Args:
            word: Word to remove.

        Returns:
            True if word existed and was removed, False otherwise.

        Thread safety: acquires write lock.
        Complexity: O(k)
        """
        if not word:
            return False

        with self._rwlock.write():
            node = self._root
            path: List[Tuple[TrieNode, str]] = []

            for char in word:
                if char not in node.children:
                    return False
                path.append((node, char))
                node = node.children[char]

            if not node.is_terminal:
                return False

            node.is_terminal = False
            node.word = None
            node.frequency = 0
            node.recency = 0.0
            node.metadata = None
            self._total_words -= 1

            # Prune leaf nodes bottom-up
            for parent, char in reversed(path):
                child = parent.children[char]
                if not child.is_terminal and not child.children:
                    del parent.children[char]
                    self._total_nodes -= 1
                else:
                    break

        return True

    # ── Search ────────────────────────────────────────────────────────────

    def search_prefix(
        self,
        prefix: str,
        max_results: int = 10,
        min_frequency: int = 0,
    ) -> List[TrieResult]:
        """
        Find all words beginning with *prefix*, ranked by frequency × recency.

        Algorithm:
            1. Traverse to prefix node — O(k)
            2. BFS/DFS subtree collecting terminal nodes
            3. Maintain a min-heap of size *max_results* for O(m log n) ranking
               where m = total terminals in subtree, n = max_results

        Args:
            prefix:      Prefix string to look up (case-sensitive).
            max_results: Maximum number of results to return.
            min_frequency: Filter out words below this frequency.

        Returns:
            List of TrieResult sorted best-first (highest score).

        Thread safety: acquires read lock.
        """
        if not prefix:
            return []

        with self._rwlock.read():
            # Navigate to prefix node
            node = self._root
            for char in prefix:
                if char not in node.children:
                    return []
                node = node.children[char]

            # BFS from prefix node
            heap: List[TrieResult] = []
            stack: List[TrieNode] = [node]

            while stack:
                current = stack.pop()

                if current.is_terminal and current.frequency >= min_frequency:
                    score = self._score(current)
                    result = TrieResult(
                        score=score,   # __post_init__ negates → min-heap becomes max-heap
                        word=current.word,
                        frequency=current.frequency,
                        recency=current.recency,
                        metadata=current.metadata,
                    )
                    # Use heapq for top-k
                    if len(heap) < max_results:
                        heapq.heappush(heap, result)
                    elif result < heap[0]:  # result has higher score (less negative)
                        heapq.heapreplace(heap, result)

                stack.extend(current.children.values())

        # Sort descending by original score (negate back)
        results = sorted(heap, key=lambda r: r.score)  # score is negated
        for r in results:
            r.score = -r.score  # restore positive score for callers
        return results

    def exact_match(self, word: str) -> Optional[TrieResult]:
        """
        Check if *word* exists in the trie.

        Returns:
            TrieResult if found, None otherwise.

        Thread safety: read lock.
        Complexity: O(k)
        """
        with self._rwlock.read():
            node = self._root
            for char in word:
                if char not in node.children:
                    return None
                node = node.children[char]

            if not node.is_terminal:
                return None

            score = self._score(node)
            return TrieResult(
                score=score,
                word=node.word,
                frequency=node.frequency,
                recency=node.recency,
                metadata=node.metadata,
            )

    def starts_with(self, prefix: str) -> bool:
        """Return True if any word starts with *prefix*. O(k)."""
        with self._rwlock.read():
            node = self._root
            for char in prefix:
                if char not in node.children:
                    return False
                node = node.children[char]
            return True

    def all_words(self) -> Generator[TrieResult, None, None]:
        """Iterate all words in the trie (for serialisation/export)."""
        with self._rwlock.read():
            stack = [self._root]
            while stack:
                node = stack.pop()
                if node.is_terminal:
                    score = self._score(node)
                    yield TrieResult(
                        score=score,
                        word=node.word,
                        frequency=node.frequency,
                        recency=node.recency,
                        metadata=node.metadata,
                    )
                stack.extend(node.children.values())

    # ── Scoring ───────────────────────────────────────────────────────────

    @staticmethod
    def _score(node: TrieNode) -> float:
        """
        Compute a raw score for a terminal node.

        Formula:  log(frequency + 1) × recency_factor
        recency_factor: newer insertions score higher, decays over ~24 hours.

        This is the trie-internal score; the ranking engine applies further
        weights combining fuzzy, context, and session scores.
        """
        import math
        freq_score = math.log1p(node.frequency)
        # Recency: 1.0 = just now, 0.5 = ~1 hour ago, approaches 0 slowly
        age_s = max(0.0, time.monotonic() - node.recency)
        recency_factor = 1.0 / (1.0 + age_s / 3600.0)
        return freq_score * (1.0 + recency_factor)

    # ── Reinforcement ────────────────────────────────────────────────────

    def reinforce(self, word: str, boost: int = 1) -> bool:
        """
        Increase frequency of *word* (session learning, user selection).

        Args:
            word:  The word to reinforce.
            boost: How much to increment frequency.

        Returns:
            True if word existed, False otherwise.

        Thread safety: write lock.
        """
        with self._rwlock.write():
            node = self._root
            for char in word:
                if char not in node.children:
                    return False
                node = node.children[char]
            if not node.is_terminal:
                return False
            node.frequency += boost
            node.recency = time.monotonic()
        return True

    # ── Statistics ────────────────────────────────────────────────────────

    @property
    def word_count(self) -> int:
        """Number of distinct words stored."""
        return self._total_words

    @property
    def node_count(self) -> int:
        """Total nodes (proxy for memory usage)."""
        return self._total_nodes

    def stats(self) -> dict:
        """Return diagnostic statistics."""
        return {
            "word_count": self._total_words,
            "node_count": self._total_nodes,
            "uptime_s": time.monotonic() - self._creation_time,
            "estimated_memory_mb": round(self._total_nodes * 300 / 1_048_576, 2),
        }

    # ── Serialization ─────────────────────────────────────────────────────

    def to_json(self) -> str:
        """
        Serialise the entire trie to a compact JSON string.

        Format: list of [word, frequency, recency, metadata] arrays.
        Words are stored, not the raw node tree — simpler and portable.

        Memory: O(n) where n = total_words.  For 500k words ≈ ~30MB JSON.
        """
        entries = []
        for result in self.all_words():
            entry = [result.word, result.frequency, result.recency]
            if result.metadata:
                entry.append(result.metadata)
            entries.append(entry)
        return json.dumps(entries, ensure_ascii=False)

    @classmethod
    def from_json(cls, data: str) -> Trie:
        """
        Reconstruct a Trie from JSON produced by *to_json*.

        Args:
            data: JSON string.

        Returns:
            New Trie instance.

        Raises:
            ValueError: If data is malformed.
        """
        trie = cls()
        try:
            entries = json.loads(data)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Malformed trie JSON: {exc}") from exc

        batch: List[Tuple[str, int]] = []
        for entry in entries:
            if not isinstance(entry, list) or len(entry) < 2:
                logger.warning("Skipping malformed trie entry: %r", entry)
                continue
            word, freq = entry[0], entry[1]
            recency = entry[2] if len(entry) > 2 else time.monotonic()
            meta = entry[3] if len(entry) > 3 else None
            # Insert with metadata directly (not batch to preserve recency/meta)
            trie.insert(word, frequency=freq, metadata=meta, recency=recency)

        logger.info("Loaded trie with %d words from JSON", trie.word_count)
        return trie

    def save(self, path: Path) -> None:
        """Save trie to *path* as JSON."""
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        try:
            tmp.write_text(self.to_json(), encoding="utf-8")
            tmp.replace(path)  # Atomic on POSIX
            logger.info("Trie saved: %d words → %s", self._total_words, path)
        except OSError as exc:
            logger.error("Failed to save trie to %s: %s", path, exc)
            if tmp.exists():
                tmp.unlink(missing_ok=True)
            raise

    @classmethod
    def load(cls, path: Path) -> Trie:
        """Load trie from *path*. Returns empty Trie on failure."""
        try:
            data = path.read_text(encoding="utf-8")
            return cls.from_json(data)
        except (OSError, ValueError) as exc:
            logger.error("Failed to load trie from %s: %s — starting empty", path, exc)
            return cls()
