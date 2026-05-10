# API Reference

Complete public API for programmatic use of UNGA BUNGA AUTO-COMPLETE.

---

## Top-Level Factory

### `create_engine()`

```python
from unga_bunga_autocomplete import create_engine

async def create_engine(
    data_dir: Optional[Path] = None,
    corpus_text: str = "",
    corpus_file: Optional[str] = None,
) -> SuggestionEngine
```

Creates, configures, and starts a `SuggestionEngine`. The simplest way to get started.

**Parameters:**
- `data_dir` — path to data directory for persistence. Defaults to `~/.unga_bunga/`.
- `corpus_text` — optional text string to train on before returning.
- `corpus_file` — optional path to a `.txt` file to train on before returning.

**Returns:** A running `SuggestionEngine`.

**Example:**
```python
import asyncio
from unga_bunga_autocomplete import create_engine

async def main():
    engine = await create_engine(
        corpus_text="hello world help python programming"
    )
    result = await engine.query_immediate("hel")
    for s in result.suggestions:
        print(s.word, s.final_score)
    await engine.shutdown()

asyncio.run(main())
```

---

## SuggestionEngine

```python
from unga_bunga_autocomplete.engine.suggestion_engine import SuggestionEngine
```

### Constructor

```python
SuggestionEngine(
    trie: Optional[Trie] = None,
    max_suggestions: int = 10,
    min_prefix_length: int = 1,
    debounce_ms: int = 80,
    fuzzy_threshold: float = 0.55,
    max_fuzzy_distance: int = 3,
    fuzzy_supplement_threshold: int = 3,
    cache_size: int = 4096,
    worker_threads: int = 2,
)
```

### Lifecycle

```python
await engine.start()     # must be called before query()
await engine.shutdown()  # graceful stop, waits for in-flight queries
```

### Querying

```python
result: QueryResult = await engine.query(
    prefix: str,
    context_tokens: Optional[Sequence[str]] = None,
    query_id: str = "",
)
```

Debounced query. If a new query arrives within `debounce_ms`, the previous is cancelled.

```python
result: QueryResult = await engine.query_immediate(
    prefix: str,
    context_tokens: Optional[Sequence[str]] = None,
    query_id: str = "",
)
```

Same as `query()` but skips debounce. Use in non-typing contexts (search boxes, command palettes, tests).

### QueryResult

```python
@dataclass
class QueryResult:
    prefix: str                    # the prefix that was queried
    suggestions: List[RankedCandidate]  # sorted best-first
    elapsed_ms: float              # time spent in engine (0 for cache hits)
    cache_hit: bool                # True if result came from LRU cache
    trie_count: int                # how many results came from trie
    fuzzy_count: int               # how many results came from fuzzy
    query_id: str                  # echo of the query_id parameter
```

### Training

```python
count: int = await engine.train_words(
    words: List[Tuple[str, int]]   # (word, frequency) pairs
)

count: int = await engine.train_text(
    text: str,
    min_length: int = 2,
)
```

Both invalidate the LRU cache after inserting.

### Selection Feedback

```python
engine.record_selection(
    word: str,
    context_tokens: Optional[Sequence[str]] = None,
)
```

Call when the user accepts a suggestion. Reinforces the word in the trie (+1 frequency), updates session scorer, and records bigram transition.

### Statistics

```python
stats: dict = engine.stats()
# {
#   "trie": {"word_count": 45320, "node_count": 198441, "estimated_memory_mb": 56.8},
#   "cache": {"size": 892, "hits": 1204, "misses": 441, "hit_rate": 0.734},
#   "queries": {"total": 1645, "avg_latency_ms": 0.72},
#   "session": {"selections": 23}
# }
```

### Properties

```python
engine.trie      # Trie instance (for admin / persistence operations)
engine.ranking   # RankingPipeline instance (for session reset, context access)
```

---

## RankedCandidate

```python
@dataclass
class RankedCandidate:
    word: str                          # the suggestion
    final_score: float                 # overall score [0.0, 1.0]
    score_breakdown: Dict[str, float]  # per-scorer sub-scores
    source: str                        # "trie" | "fuzzy" | "session"
    trie_result: Optional[TrieResult]
    fuzzy_match: Optional[FuzzyMatch]

candidate.explain() -> str
# "'hello' score=0.8100 source=trie [prefix: 0.8000, frequency: 0.4010, ...]"
```

---

## Trie

```python
from unga_bunga_autocomplete.engine.trie import Trie, TrieResult
```

### Insert

```python
trie.insert(
    word: str,
    frequency: int = 1,
    metadata: Optional[dict] = None,
    recency: Optional[float] = None,   # monotonic timestamp; defaults to now
)

trie.batch_insert(words: List[Tuple[str, int]])  # (word, frequency)
```

### Query

```python
results: List[TrieResult] = trie.search_prefix(
    prefix: str,
    max_results: int = 10,
    min_frequency: int = 0,
)

result: Optional[TrieResult] = trie.exact_match(word: str)
exists: bool = trie.starts_with(prefix: str)
```

### TrieResult

```python
@dataclass
class TrieResult:
    score: float        # trie-internal score (positive after search_prefix)
    word: str
    frequency: int
    recency: float      # monotonic timestamp
    metadata: Optional[dict]
```

### Mutate

```python
deleted: bool = trie.delete(word: str)
reinforced: bool = trie.reinforce(word: str, boost: int = 1)
```

### Serialise

```python
json_str: str = trie.to_json()
trie2 = Trie.from_json(json_str: str)   # raises ValueError on bad JSON
trie.save(path: Path)
trie3 = Trie.load(path: Path)            # returns empty Trie on error
```

### Stats

```python
trie.word_count  # int
trie.node_count  # int
trie.stats()     # dict
```

---

## FuzzyEngine

```python
from unga_bunga_autocomplete.engine.fuzzy import FuzzyEngine, similarity, levenshtein
```

### FuzzyEngine

```python
engine = FuzzyEngine(
    threshold: float = 0.5,      # minimum combined score to include
    max_distance: int = 3,       # maximum edit distance (early exit)
    dl_weight: float = 0.65,     # weight for DL similarity
    ngram_weight: float = 0.35,  # weight for Jaccard similarity
)

matches: List[FuzzyMatch] = engine.search(
    query: str,
    vocabulary: Sequence[str],
    max_results: int = 10,
)

match: FuzzyMatch = engine.score(query: str, candidate: str)
```

### FuzzyMatch

```python
@dataclass
class FuzzyMatch:
    word: str
    similarity: float      # DL similarity [0,1]
    edit_distance: float   # raw DL distance
    ngram_score: float     # Jaccard bigram score [0,1]
    combined: float        # weighted combination [0,1]
```

### Standalone Functions

```python
dist: int = levenshtein(s1: str, s2: str, max_dist: int = 10)
dist: float = damerau_levenshtein(s1: str, s2: str, max_dist: int = 10)
score: float = similarity(s1: str, s2: str, max_dist: int = 10)  # [0,1]
score: float = jaccard_similarity(s1: str, s2: str, n: int = 2)  # [0,1]
```

---

## TrainingPipeline

```python
from unga_bunga_autocomplete.training import TrainingPipeline, Tokeniser, TokeniserConfig
```

### TrainingPipeline

```python
pipeline = TrainingPipeline(config: Optional[TokeniserConfig] = None)

result: TrainingResult = await pipeline.train_text(
    text: str,
    engine: SuggestionEngine,
    corpus_name: str = "inline",
)

result: TrainingResult = await pipeline.train_file(
    path: Path,
    engine: SuggestionEngine,
    corpus_name: Optional[str] = None,
)

pipeline.stats  # dict
```

### TrainingResult

```python
@dataclass
class TrainingResult:
    corpus_name: str
    token_count: int
    vocab_size: int
    elapsed_s: float
    words_in_trie: int
    warnings: List[str]
```

### Tokeniser

```python
tokeniser = Tokeniser(config: Optional[TokeniserConfig] = None)
tokens: List[str] = tokeniser.tokenise(text: str)
```

### TokeniserConfig

```python
@dataclass
class TokeniserConfig:
    lowercase: bool = True
    normalize_unicode: bool = True
    strip_punctuation: bool = True
    strip_numbers: bool = False
    min_token_length: int = 2
    max_token_length: int = 64
    split_on_camel_case: bool = True
    split_on_underscores: bool = True
    ngram_sizes: List[int] = [2, 3]
    max_vocab_size: int = 500_000
```

---

## Storage

```python
from unga_bunga_autocomplete.persistence import Storage
```

```python
storage = Storage(db_path: Path, wal_mode: bool = True)
storage.open()
storage.close()

storage.upsert_word(word, frequency, recency, source)
storage.upsert_words_batch(words: List[Tuple[str, int, str]])
words = storage.load_words(limit=500_000)  # List[Tuple[str, int, float]]

storage.upsert_bigrams(bigrams: List[Tuple[str, str, int]])
bigrams = storage.load_bigrams(limit=2_000_000)

snapshot_id = storage.create_snapshot(trie_json, word_count, label, max_snapshots)
trie_json = storage.load_latest_snapshot()  # None if no valid snapshot
snapshots = storage.list_snapshots()

health = storage.health_check()   # {"ok": bool, "details": str}
stats = storage.stats()           # {"word_count", "snapshot_count", "db_size_mb", ...}
```

---

## LifecycleManager

```python
from unga_bunga_autocomplete.core.lifecycle import LifecycleManager

manager = LifecycleManager(
    data_dir: Optional[Path] = None,          # default: ~/.unga_bunga
    autosave_interval_s: int = 300,
    enable_persistence: bool = True,
)

# As context manager (recommended)
async with LifecycleManager() as manager:
    engine = manager.engine
    ...

# Manual
await manager.start()
engine = manager.engine
await manager.shutdown()

manager.is_running   # bool
manager.stats()      # dict with engine + storage stats
```

---

## EventBus

```python
from unga_bunga_autocomplete.core.events import get_event_bus, QueryEvent, SuggestionReadyEvent

bus = get_event_bus()

# Subscribe
bus.subscribe(QueryEvent, my_async_handler)
bus.unsubscribe(QueryEvent, my_async_handler)

# Dispatch
await bus.dispatch(QueryEvent(prefix="hel"))
bus.dispatch_sync(QueryEvent(prefix="hel"))   # thread-safe, schedules on loop

bus.dead_letters   # events with no subscribers
```

---

## ConfigManager

```python
from unga_bunga_autocomplete.core.config import get_config

cfg = get_config()
cfg.load(path=Path("my_config.json"))   # optional; loads defaults if not called
cfg.save()

cfg.engine.max_suggestions     # int
cfg.engine.fuzzy_threshold     # float
cfg.persistence.data_dir       # str
cfg.training.max_vocab_size    # int

errors = cfg.validate()        # List[str] — empty means valid
cfg.subscribe(callback)        # called on any config change
```
