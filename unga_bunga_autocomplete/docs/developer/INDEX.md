# Developer Documentation

Complete reference for contributors and developers working on UNGA BUNGA AUTO-COMPLETE.

---

## Contents

| Document | What it covers |
|----------|---------------|
| [GETTING_STARTED.md](GETTING_STARTED.md) | Environment setup, dev workflow, first run |
| [ARCHITECTURE.md](ARCHITECTURE.md) | System design, module graph, threading, query pipeline |
| [TRIE_ENGINE.md](TRIE_ENGINE.md) | Trie internals, algorithms, complexity, extension guide |
| [TESTING_GUIDE.md](TESTING_GUIDE.md) | How to run, write, and extend tests |
| [PERFORMANCE_ENGINEERING.md](PERFORMANCE_ENGINEERING.md) | Benchmarks, profiling, bottlenecks, optimisation |
| [PERSISTENCE_SYSTEM.md](PERSISTENCE_SYSTEM.md) | SQLite schema, WAL, snapshots, migrations |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Code standards, PR checklist, common pitfalls |
| [API_REFERENCE.md](API_REFERENCE.md) | Complete public API with signatures and examples |

---

## Quick Links

**Setting up dev environment:**  → [GETTING_STARTED.md](GETTING_STARTED.md)

**Understanding how a query flows through the system:**  → [ARCHITECTURE.md](ARCHITECTURE.md#query-pipeline-detailed)

**How the trie works internally:**  → [TRIE_ENGINE.md](TRIE_ENGINE.md)

**Writing a new test:**  → [TESTING_GUIDE.md](TESTING_GUIDE.md#writing-new-tests)

**Adding a new ranking scorer:**  → [CONTRIBUTING.md](CONTRIBUTING.md#adding-a-new-scorer)

**Adding a new database column:**  → [PERSISTENCE_SYSTEM.md](PERSISTENCE_SYSTEM.md#adding-a-migration)

**Using the engine as a Python library:**  → [API_REFERENCE.md](API_REFERENCE.md)

**Common import errors:**  → [CONTRIBUTING.md](CONTRIBUTING.md#common-pitfalls)

---

## Architecture at a Glance

```
run.py
  └── __main__.py (arg parse)
        └── LifecycleManager (startup/shutdown)
              ├── SuggestionEngine
              │     ├── Trie          O(k) prefix lookup, RW-locked
              │     ├── FuzzyEngine   DL + Jaccard, fires only when trie sparse
              │     ├── RankingPipeline  7-scorer weighted pipeline
              │     └── LRUCache      4096 entries, 30s TTL
              ├── Storage (SQLite WAL, snapshots with SHA-256)
              └── TrainingPipeline (tokenise → frequency → batch_insert)
```

**Key rule:** All imports across subpackage boundaries are absolute:

```python
from unga_bunga_autocomplete.engine.trie import Trie    # correct
from .engine.trie import Trie                            # wrong
```

---

## Test Status

```
111 tests — all passing

TestTrieBasic          (8 tests)
TestTrieDelete         (5 tests)
TestTrieUnicode        (4 tests)
TestTrieBatchInsert    (4 tests)
TestTrieSerialization  (5 tests)
TestTrieConcurrency    (2 tests)
TestTrieReinforce      (2 tests)
TestLevenshtein        (7 tests)
TestDamerauLevenshtein (4 tests)
TestSimilarity         (4 tests)
TestJaccard            (3 tests)
TestFuzzyEngine        (6 tests)
TestRankingPipeline    (7 tests)
TestPrefixScorer       (2 tests)
TestFrequencyScorer    (3 tests)
TestRecencyScorer      (3 tests)
TestSuggestionEngine   (8 tests)
TestTokeniser          (7 tests)
TestFrequencyBuilder   (6 tests)
TestStorage            (8 tests)
TestLRUCache           (6 tests)
TestStress             (3 tests)
```
