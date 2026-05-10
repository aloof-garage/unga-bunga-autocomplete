# Architecture

Complete system design reference for UNGA BUNGA AUTO-COMPLETE.

---

## High-Level Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                  UNGA BUNGA AUTO-COMPLETE                       │
│                                                                 │
│  ┌────────────┐  ┌──────────────┐  ┌──────────────────────┐   │
│  │  CLI/TUI   │  │  Python API  │  │  GUI hook (future)   │   │
│  └─────┬──────┘  └──────┬───────┘  └──────────┬───────────┘   │
│        └────────────────┼──────────────────────┘              │
│                         ▼                                      │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │                LifecycleManager                          │  │
│  │    startup → warmup → running → shutdown → recovery      │  │
│  └───────────────────────┬──────────────────────────────────┘  │
│          ┌───────────────┼───────────────┐                     │
│          ▼               ▼               ▼                     │
│  ┌──────────────┐ ┌────────────┐ ┌─────────────┐             │
│  │  Suggestion  │ │  Training  │ │   Storage   │             │
│  │  Engine      │ │  Pipeline  │ │  (SQLite)   │             │
│  │  ┌────────┐  │ │ Tokeniser  │ │  words      │             │
│  │  │  Trie  │  │ │ Frequency  │ │  bigrams    │             │
│  │  │ Fuzzy  │  │ │  Builder   │ │  snapshots  │             │
│  │  │Ranking │  │ └────────────┘ │  WAL mode   │             │
│  │  │LRUCache│  │               └─────────────┘             │
│  │  └────────┘  │                                            │
│  └──────────────┘                                            │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │       Core: EventBus · ConfigManager                     │  │
│  └──────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

---

## Module Dependency Graph

```
run.py
  └── __main__.py
        └── core.lifecycle.LifecycleManager
              ├── engine.suggestion_engine.SuggestionEngine
              │     ├── engine.trie.Trie              (stdlib only)
              │     ├── engine.fuzzy.FuzzyEngine       (stdlib only)
              │     ├── engine.ranking.RankingPipeline
              │     │     ├── PrefixScorer             (stdlib only)
              │     │     ├── FrequencyScorer          (math)
              │     │     ├── RecencyScorer            (time)
              │     │     ├── SessionScorer            (stdlib only)
              │     │     ├── FuzzyScorer              (stdlib only)
              │     │     ├── ContextScorer            (stdlib only)
              │     │     └── NGramScorer              (stdlib only)
              │     └── engine.suggestion_engine.LRUCache
              ├── persistence.Storage                  (sqlite3)
              └── training.TrainingPipeline
                    ├── training.Tokeniser             (re, unicodedata)
                    └── training.FrequencyBuilder      (collections)
```

No circular imports. Every leaf depends only on the Python standard library.

---

## Threading Model

```
Main thread (asyncio event loop)
  ├── Shell input / prompt_toolkit rendering
  ├── Query dispatch: await engine.query()
  └── Autosave task: asyncio.create_task()

ThreadPoolExecutor (2 workers, "ub-autocomplete")
  ├── Trie.search_prefix()       CPU-bound, RW-locked
  ├── FuzzyEngine.search()       CPU-bound, thread-local matrix pool
  ├── RankingPipeline.rank()     CPU-bound, pure Python math
  ├── Trie.batch_insert()        CPU-bound, write-locked
  ├── Storage.*()                I/O-bound, sqlite3
  └── Trie.to_json/from_json()   CPU + I/O
```

**Rule:** The asyncio event loop never blocks. All CPU or I/O work goes through `loop.run_in_executor()`.

---

## Query Pipeline

```
User types "hel"
  │
  ├─ 1. Cache lookup  key=("hel", frozenset(context[:3]))
  │       hit  → return immediately (0 ms)
  │       miss → continue
  │
  ├─ 2. Debounce sleep (80 ms)
  │       New query during sleep? Cancel this one.
  │
  └─ 3. run_in_executor → _search_blocking()
          │
          ├─ Trie.search_prefix("hel", max=20)
          │     Walk to "hel" node: O(3)
          │     BFS subtree, min-heap top-20 by score
          │     score = log(freq+1) × recency_decay
          │
          ├─ if len(trie_results) < 3:
          │     FuzzyEngine.search("hel", vocab)
          │       Damerau-Levenshtein (QWERTY-weighted) + Jaccard bigram
          │
          └─ RankingPipeline.rank()
                For each candidate compute 7 sub-scores,
                weighted sum, normalise → final_score ∈ [0,1]
                Sort descending, return top-k
```

---

## Trie Design

### Node Memory Layout

```python
class TrieNode:
    __slots__ = ("children", "is_terminal", "word",
                 "frequency", "recency", "metadata")
```

`__slots__` saves ~40% memory vs plain objects. 200k nodes ≈ 60 MB.

### Scoring

```
score = log(frequency + 1)  ×  (1 + 1/(1 + age_seconds/3600))
```

Recency factor: 2.0 when freshly inserted, approaches 1.0 after hours.

### Top-k via Min-Heap

`search_prefix` maintains a min-heap of size `max_results`. For each terminal node found during BFS: push if heap not full, else `heapreplace` if current node scores higher than the root (the worst result in the heap). Final result: O(m log k).

### RW Lock

Multiple readers hold a shared counter simultaneously. Writers acquire exclusive access with writer preference — pending writers block new readers to prevent writer starvation.

---

## Fuzzy Engine Design

### Algorithms Combined

| Algorithm | Cost | Catches |
|-----------|------|---------|
| Damerau-Levenshtein | O(n×m) | insertions, deletions, substitutions, transpositions |
| Jaccard bigram | O(n+m) | anagram-like typos, partial matches |

**Combined:** `0.65 × DL_similarity + 0.35 × Jaccard`

### QWERTY Keyboard Weights

Substituting adjacent keyboard keys costs 0.5 instead of 1.0:

```python
_QWERTY_ADJACENCY = {
    "q": "wa", "w": "qase", "e": "wsdr", ...
}
```

### Thread-Local Matrix Pool

Each thread pre-allocates a 256×256 float matrix via `threading.local()`. No allocation per call, no locks needed — each thread owns its own matrix.

### Fuzzy Trigger Threshold

Fuzzy search (O(|vocab|)) only fires when the trie returns fewer than `fuzzy_supplement_threshold` results (default 3). Prevents expensive scans on common prefixes.

---

## Ranking Weights

| Scorer | Default Weight | Signal |
|--------|---------------|--------|
| Prefix | 3.0 | Exact prefix match |
| Session | 2.5 | Words typed this session |
| Frequency | 2.0 | Corpus training frequency |
| Context | 1.8 | Bigram transition probability |
| Recency | 1.5 | Time since last use |
| NGram | 1.2 | Character trigram overlap |
| Fuzzy | 1.0 | DL+Jaccard similarity |

`final_score = Σ(weight_i × sub_score_i) / total_weight`

All sub-scores and the final score are in [0.0, 1.0].

---

## Persistence Design

### Schema

```sql
words       (word PK, frequency, recency, source, created_at)
bigrams     (prev_word, next_word PK, count)
snapshots   (id PK, created_at, trie_json, word_count, checksum, label)
schema_version (version, applied)
```

### Snapshot Integrity

Every snapshot: `checksum = SHA-256(trie_json)`. On load: verify checksum before parsing JSON. Walk backwards through up to 10 snapshots until a valid one is found. Start empty if all fail.

### Atomic Writes

JSON written to `.tmp` then `Path.replace()` — atomic on POSIX and Windows (same filesystem).

### WAL Mode

`PRAGMA journal_mode=WAL` — readers never block on writes. Critical for autocomplete.

### Migration System

```python
_SCHEMA_VERSION = 3

_MIGRATIONS = [
    (1, 2, _migration_v1_to_v2),  # added source column
    (2, 3, _migration_v2_to_v3),  # added label column
]
```

To add a migration: append a tuple, increment `_SCHEMA_VERSION`.

---

## Lifecycle Sequence

```
start()
  ├─ Storage.open() → WAL, migrations, health_check
  ├─ load_latest_snapshot() → verify SHA-256 → Trie.from_json()
  │     fallback: load_words_from_db() → batch_insert()
  ├─ SuggestionEngine.start() → ThreadPoolExecutor
  ├─ load_bigrams() → context_scorer.record_transition()
  ├─ create_task(autosave_loop)  every 300s
  └─ register_signals (POSIX: add_signal_handler, Win32: signal.signal)

shutdown()
  ├─ cancel autosave_task
  ├─ save_snapshot("shutdown")
  ├─ flush_words_to_db()
  ├─ engine.shutdown() → ThreadPoolExecutor.shutdown(wait=True)
  └─ storage.close() → WAL checkpoint TRUNCATE
```

---

## Windows Notes

| Issue | Fix |
|-------|-----|
| `loop.add_signal_handler()` → `NotImplementedError` | `sys.platform == "win32"` → use `signal.signal(SIGINT, ...)` |
| `SIGTERM` not available | Skip SIGTERM on Win32 |
| Terminal colour | `rich` uses `colorama` automatically |

---

## Import Rule

Always use **absolute imports** across subpackage boundaries:

```python
# CORRECT — works from any calling module
from unga_bunga_autocomplete.persistence import Storage
from unga_bunga_autocomplete.engine.trie import Trie

# WRONG — relative across subpackage boundary
from .persistence import Storage   # only works if caller is sibling of persistence
```

Relative imports (`./..`) are only safe within the same subpackage.

---

## LRU Cache

`collections.OrderedDict`-based with TTL:

- Capacity: 4096 entries
- TTL: 30 seconds
- Key: `(prefix, frozenset(context[:3]))`
- Invalidation: entire cache cleared after any index update
- Thread-safe via `threading.Lock`
