# Contributing

Guidelines for working on UNGA BUNGA AUTO-COMPLETE.

---

## Before You Start

1. Read `ARCHITECTURE.md` — understand the module graph and threading model before changing anything.
2. Read `TRIE_ENGINE.md` if touching the trie.
3. Run the full test suite and confirm it is green: `pytest unga_bunga_autocomplete/tests/ -q`
4. Run the benchmarks and note baseline numbers: `python run.py --benchmark`

---

## Code Standards

### Imports

Always use **absolute imports** across subpackage boundaries:

```python
# CORRECT
from unga_bunga_autocomplete.engine.trie import Trie
from unga_bunga_autocomplete.persistence import Storage

# WRONG (causes ModuleNotFoundError on some call paths)
from .engine.trie import Trie
from ..persistence import Storage
```

Relative imports are only safe within the same subpackage (e.g. inside `engine/ranking/` importing from `engine/fuzzy/` use `from ..fuzzy import FuzzyMatch`).

### Type Hints

All function signatures must be fully type-annotated:

```python
def search_prefix(
    self,
    prefix: str,
    max_results: int = 10,
    min_frequency: int = 0,
) -> List[TrieResult]:
```

Use `from __future__ import annotations` at the top of every file (enables forward references without quotes).

### Docstrings

Every public class and method needs a docstring. Format:

```python
def batch_insert(self, words: List[Tuple[str, int]]) -> None:
    """
    Insert many (word, frequency) pairs efficiently.

    Acquires write lock ONCE for the entire batch, which is dramatically
    faster than per-word locking for large corpora.

    Args:
        words: Iterable of (word, frequency) pairs.

    Complexity: O(sum of word lengths)
    """
```

Include `Args:`, `Returns:`, `Raises:`, `Thread safety:`, and `Complexity:` sections where relevant.

### Error Handling

Never let exceptions propagate silently. Every except block must either:
- Re-raise with context
- Log with `logger.error(...)` and recover gracefully

```python
# CORRECT
try:
    trie = Trie.from_json(snapshot_json)
except Exception as exc:
    logger.error("Snapshot restore failed: %s — starting empty", exc)
    trie = Trie()

# WRONG — silent swallow
try:
    trie = Trie.from_json(snapshot_json)
except Exception:
    trie = Trie()
```

### Logging

Use module-level loggers:

```python
import logging
logger = logging.getLogger(__name__)
```

Log levels:
- `DEBUG` — per-query detail, cache hits/misses, node operations
- `INFO` — lifecycle events, training completion, snapshot saves
- `WARNING` — degraded operation (persistence failed, falling back)
- `ERROR` — operation failed but system continues
- `CRITICAL` — only for unrecoverable failures (never used in normal code)

### Thread Safety

If a method reads or mutates shared state, document it:

```python
def insert(self, word: str, ...) -> None:
    """
    ...
    Thread safety: acquires write lock.
    """
```

Never access `_rwlock`-protected data outside the lock. Never call blocking I/O from the asyncio event loop thread — use `run_in_executor`.

---

## Common Pitfalls

### Wrong import path in lifecycle or __main__

`lifecycle/__init__.py` must use absolute imports because it reaches across subpackage boundaries:

```python
# Inside core/lifecycle/__init__.py
from unga_bunga_autocomplete.persistence import Storage   # CORRECT
from .persistence import Storage                          # WRONG
```

### Forgetting to invalidate the cache

After any index mutation (insert, delete, reinforce), the LRU cache must be invalidated:

```python
self._trie.batch_insert(batch)
self._cache.invalidate()   # ← don't forget this
```

### Recursion in trie traversal

Python's default recursion limit is 1000. A trie with 64-character words can exceed this with naive recursive DFS. All trie traversal in this codebase uses explicit stacks:

```python
stack = [self._root]
while stack:
    node = stack.pop()
    stack.extend(node.children.values())
```

Never change this to recursion.

### Score double-negation in TrieResult

`TrieResult.__post_init__` negates `score` (for min-heap semantics). Pass the **raw positive score** to the constructor:

```python
result = TrieResult(score=raw_score, ...)   # __post_init__ negates → stored as -raw_score
# NOT:
result = TrieResult(score=-raw_score, ...)  # double negation → stored as +raw_score (WRONG)
```

### Windows signal handling

`asyncio.loop.add_signal_handler()` is not available on Windows. The lifecycle manager detects `sys.platform == "win32"` and uses `signal.signal()` instead. Do not use `add_signal_handler` directly anywhere else.

---

## Pull Request Checklist

Before opening a PR:

- [ ] `pytest unga_bunga_autocomplete/tests/ -q` — all 111 tests pass
- [ ] `python run.py --benchmark` — no significant regression from baseline
- [ ] New code has type hints
- [ ] New public methods have docstrings with Args/Returns
- [ ] Thread safety documented on any method touching shared state
- [ ] Absolute imports used across subpackage boundaries
- [ ] Cache invalidated after any index mutation
- [ ] No recursion in trie traversal code
- [ ] `ARCHITECTURE.md` updated if module graph changed
- [ ] New feature has at least one test in `test_all.py`

---

## Adding a New Scorer

The ranking pipeline is designed to accept new scorers without changing existing code.

1. Create your scorer class following the pattern in `engine/ranking/__init__.py`:

```python
class MyNewScorer:
    def score(self, candidate: str, ...) -> float:
        """Returns float in [0.0, 1.0]."""
        ...
```

2. Add a weight field to `RankingWeights`:

```python
@dataclass
class RankingWeights:
    ...
    my_new: float = 1.0
```

3. Instantiate it in `RankingPipeline.__init__`:

```python
self.my_scorer = MyNewScorer()
```

4. Call it in `_score_candidate`:

```python
s_my = self.my_scorer.score(cand.word, ...)
raw += w.my_new * s_my
cand.score_breakdown["my_new"] = round(s_my, 4)
```

5. Update `RankingWeights.total()` to include the new weight.

6. Add tests in `TestRankingPipeline`.

---

## Versioning

Version is set in `unga_bunga_autocomplete/__init__.py`:

```python
__version__ = "1.0.0"
```

And in `setup.py`:

```python
version="1.0.0",
```

Schema version (for SQLite migrations) is in `persistence/__init__.py`:

```python
_SCHEMA_VERSION = 3
```

Increment `_SCHEMA_VERSION` and add a migration entry in `_MIGRATIONS` whenever you add or alter a database table or column.
