# Testing Guide

File: `unga_bunga_autocomplete/tests/test_all.py`

---

## Running Tests

```bash
# From unga_bunga_autocomplete_project/

# All 111 tests
pytest unga_bunga_autocomplete/tests/ -v

# Specific test class
pytest unga_bunga_autocomplete/tests/ -v -k "TestTrie"
pytest unga_bunga_autocomplete/tests/ -v -k "TestFuzzy"
pytest unga_bunga_autocomplete/tests/ -v -k "TestRanking"
pytest unga_bunga_autocomplete/tests/ -v -k "TestSuggestion"
pytest unga_bunga_autocomplete/tests/ -v -k "TestStorage"
pytest unga_bunga_autocomplete/tests/ -v -k "TestStress"

# Stop on first failure
pytest unga_bunga_autocomplete/tests/ -x

# With coverage
coverage run -m pytest unga_bunga_autocomplete/tests/
coverage report -m
coverage html   # generates htmlcov/index.html
```

---

## Test Structure

```
tests/test_all.py
│
├── TestTrieBasic          insert, exact match, prefix search, frequency ordering
├── TestTrieDelete         delete, pruning, word count
├── TestTrieUnicode        Unicode, emoji, NFC normalisation
├── TestTrieBatchInsert    batch correctness, speed
├── TestTrieSerialization  JSON roundtrip, save/load, corruption recovery
├── TestTrieConcurrency    10-thread concurrent insert, simultaneous R/W
├── TestTrieReinforce      session learning reinforcement
│
├── TestLevenshtein        known distances, empty strings, early exit
├── TestDamerauLevenshtein transpositions, keyboard proximity, identical
├── TestSimilarity         normalised scores, identical=1.0, close words
├── TestJaccard            bigram overlap correctness
├── TestFuzzyEngine        typo correction, sorting, threshold, empty query
│
├── TestRankingPipeline    frequency ordering, score range, breakdown present
├── TestPrefixScorer       exact vs non-exact prefix
├── TestFrequencyScorer    higher freq = higher score, normalisation
├── TestRecencyScorer      fresh = high score, old = low score
│
├── TestSuggestionEngine   train + query, cache hits, selection feedback, stats
│
├── TestTokeniser          basic, lowercase, punctuation, min_length, camel
├── TestFrequencyBuilder   accumulation, merge, bigrams, top_words
│
├── TestStorage            upsert, batch, snapshot create/load, health check
├── TestLRUCache           put/get, eviction, TTL, hit rate, invalidate
│
└── TestStress             100k word insert, search speed, 10-thread concurrent
```

---

## Writing New Tests

### Sync test

```python
class TestMyFeature:
    def setup_method(self):
        from unga_bunga_autocomplete.engine.trie import Trie
        self.trie = Trie()

    def test_my_case(self):
        self.trie.insert("hello")
        result = self.trie.exact_match("hello")
        assert result is not None
        assert result.word == "hello"
```

### Async test (requires `pytest-asyncio`)

```python
class TestMyAsyncFeature:
    @pytest.fixture
    async def engine(self):
        from unga_bunga_autocomplete.engine.suggestion_engine import SuggestionEngine
        eng = SuggestionEngine(debounce_ms=0)  # disable debounce in tests
        await eng.start()
        yield eng
        await eng.shutdown()

    @pytest.mark.asyncio
    async def test_my_case(self, engine):
        await engine.train_text("hello world")
        result = await engine.query_immediate("hel")
        assert len(result.suggestions) > 0
```

### Persistence test (uses tmp_path fixture)

```python
class TestMyPersistence:
    @pytest.fixture
    def storage(self, tmp_path):
        from unga_bunga_autocomplete.persistence import Storage
        db = Storage(tmp_path / "test.db")
        db.open()
        yield db
        db.close()

    def test_my_case(self, storage):
        storage.upsert_word("hello", frequency=5)
        words = storage.load_words()
        assert any(w[0] == "hello" for w in words)
```

---

## Key Test Design Rules

**1. Never use real `~/.unga_bunga/` in tests.**
Always pass `tmp_path` to Storage, or use `enable_persistence=False` on LifecycleManager.

**2. Disable debounce in engine tests.**
`SuggestionEngine(debounce_ms=0)` — otherwise tests wait 80ms per query.

**3. Use `query_immediate` not `query` in tests.**
`query_immediate` skips debounce and always runs immediately.

**4. Insert with explicit recency when testing frequency ordering.**
The trie score combines frequency and recency. To isolate frequency effects:
```python
import time
ts = time.monotonic()
trie.insert("hello", frequency=100, recency=ts)  # same timestamp
trie.insert("help",  frequency=1,   recency=ts)  # removes recency variable
```

**5. Stress tests must have machine-speed-independent thresholds.**
Use relative assertions (`> 50 queries`) not hard timing (`< 100ms`) which fails on slow CI machines.

**6. Thread tests must collect errors in a shared list.**
```python
errors = []

def worker():
    try:
        ...
    except Exception as e:
        errors.append(e)

# After joining all threads:
assert not errors, f"Thread errors: {errors}"
```

---

## pytest.ini Configuration

```ini
[pytest]
asyncio_mode = auto
testpaths = unga_bunga_autocomplete/tests
python_files = test_*.py
python_classes = Test*
python_functions = test_*
```

`asyncio_mode = auto` means all `async def test_*` functions are automatically treated as async tests without needing `@pytest.mark.asyncio` on each one. (The decorator is included anyway for explicitness.)

---

## Coverage

```bash
coverage run -m pytest unga_bunga_autocomplete/tests/
coverage report -m --include="unga_bunga_autocomplete/*"
```

Current coverage targets:

| Module | Target |
|--------|--------|
| `engine/trie` | > 90% |
| `engine/fuzzy` | > 90% |
| `engine/ranking` | > 85% |
| `engine/suggestion_engine` | > 85% |
| `persistence` | > 80% |
| `training` | > 80% |
| `cli` | > 50% (TUI is hard to test without a real terminal) |

---

## Adding Tests for a New Feature

1. Add a new `class TestMyFeature:` block in `test_all.py`.
2. `setup_method` constructs the component under test from scratch.
3. Each `test_*` method tests exactly one behaviour.
4. Run `pytest -v -k TestMyFeature` to verify in isolation.
5. Run the full suite `pytest -q` to check for regressions.
6. If you added async code, add at least one async test with `@pytest.mark.asyncio`.
7. If you added persistence code, add a storage fixture test.
8. If you changed a scoring formula, update the ordering/range assertions.
