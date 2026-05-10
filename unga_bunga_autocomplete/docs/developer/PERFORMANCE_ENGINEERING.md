# Performance Engineering

Measured numbers, known bottlenecks, and the optimisation playbook.

---

## Benchmark Results (Measured)

Run with: `python run.py --benchmark`

| Operation | Rate | Latency | Notes |
|-----------|------|---------|-------|
| Trie batch insert | ~450k words/sec | — | Single write lock for whole batch |
| Prefix search (selective prefix) | ~1.4k q/sec | ~0.7ms | Prefix matching < 1k results |
| Prefix search (full-tree scan) | ~6 q/sec | ~160ms | Prefix matching all 100k words |
| Levenshtein (8-char strings) | ~50k pairs/sec | — | Thread-local matrix pool |
| End-to-end suggestion (10k vocab) | ~1.4k q/sec | ~0.7ms | Includes cache, ranking |
| Trie serialize 100k words | — | ~300ms / 4.5 MB | JSON format |
| Trie deserialize 100k words | — | ~700ms | Rebuild node by node |

---

## Profiling

### Quick Profile

```bash
python -m cProfile -s cumulative run.py --benchmark 2>&1 | head -40
```

### Profile a Specific Path

```python
import cProfile
import pstats
import asyncio
from unga_bunga_autocomplete import create_engine

async def workload():
    engine = await create_engine(corpus_text=" ".join(f"word{i}" for i in range(10000)))
    for i in range(500):
        await engine.query_immediate(f"word{i % 100}")

pr = cProfile.Profile()
pr.enable()
asyncio.run(workload())
pr.disable()

stats = pstats.Stats(pr)
stats.sort_stats("cumulative")
stats.print_stats(20)
```

### Memory Profile

```bash
pip install memory-profiler
python -m memory_profiler profile_script.py
```

For trie-specific memory:

```python
trie = Trie()
trie.batch_insert([(f"word{i}", 1) for i in range(100_000)])
print(trie.stats())
# {'word_count': 100000, 'node_count': ~200000, 'estimated_memory_mb': ~57.0}
```

---

## Known Bottlenecks

### 1. Prefix Search on Non-Selective Prefixes

**Problem:** If the prefix matches a huge portion of the vocabulary (e.g. single-character prefix on a homogeneous corpus), `search_prefix` visits every node in the subtree. For 100k words under "w": ~100k node visits, ~160ms.

**Current mitigation:** Fuzzy search only fires when trie returns < 3 results. For popular prefixes the trie always wins.

**Future fix:** DFS with branch-and-bound pruning. If the maximum possible score from a subtree (using the highest frequency child as upper bound) can't beat the kth result in the heap, prune the branch. This would turn worst-case O(m) into near-O(k log k) for well-ordered tries.

### 2. Fuzzy Search is O(|vocabulary|)

**Problem:** `FuzzyEngine.search()` computes DL distance against every word in the vocabulary. For 500k words this takes ~10 seconds — unacceptable.

**Current mitigation:** Fuzzy only fires when trie returns < 3 results (rare for prefixes ≥ 3 chars). The vocabulary iterated is from `trie.all_words()` which is bounded.

**Future fix:** BK-tree (metric tree) for O(|results|) fuzzy lookup instead of O(|vocab|). BK-trees exploit the triangle inequality of edit distance to prune branches.

### 3. Trie Serialisation

**Problem:** `to_json()` on 100k words produces a 4.5 MB JSON string and takes ~300ms. `from_json()` takes ~700ms. This makes startup from snapshot slow for large vocabularies.

**Current mitigation:** Snapshots are loaded once at startup. JSON is the chosen format for portability.

**Future fix:** Binary serialisation (MessagePack or custom) would reduce size to ~1–2 MB and parse time to ~100ms.

### 4. LRU Cache TTL

**Problem:** Cache invalidation on every trie update (`self._cache.invalidate()`) discards all cached results. If training and querying happen simultaneously, cache hit rate degrades.

**Current mitigation:** Background training is batched; the cache is only invalidated once per batch, not once per word.

**Future fix:** Prefix-aware invalidation — only invalidate cache entries whose key prefix is affected by the inserted words.

---

## Optimisation Playbook

### Improve Query Latency

1. Increase cache size (`engine.cache_size` in config) — reduces trie lookups on repeated queries.
2. Increase debounce time — fewer queries fired per keystroke.
3. Reduce `max_suggestions` — smaller heap operations.
4. Reduce `fuzzy_max_distance` — faster DL early exit.

### Improve Training Speed

1. Use `batch_insert` — always. Never call `insert` in a loop.
2. Increase `background_index_workers` in config — more parallel training threads.
3. Pre-filter corpus before feeding — strip stopwords and very short tokens.

### Reduce Memory

1. Lower `max_vocab_size` in training config — caps the number of unique tokens stored.
2. Lower `cache_size` — each cache entry holds a list of `RankedCandidate` objects.
3. Delete trie entries for words with frequency < 2 after training — prunes hapax legomena.

### Serialisation Speed

For very large vocabularies (500k+), switch from JSON to a binary format:

```python
# Future: binary format for 5× faster serialise/deserialise
import struct
# Each entry: 4-byte freq, 8-byte recency, 2-byte word_len, N-byte word UTF-8
```

---

## Cache Hit Rate Tuning

The LRU cache key is `(prefix, frozenset(context_tokens[:3]))`. If context changes constantly (e.g. the user types a new word between every query), cache hit rate will be low.

Increase context window stability by normalising context:

```python
# Only use last 1 context token instead of 3
ctx_key = frozenset((context_tokens or [])[-1:])
```

Monitor hit rate in real use via `:stats` in the shell.

---

## Concurrency Tuning

Default thread pool: 2 workers. For a server deployment handling multiple simultaneous users:

```python
engine = SuggestionEngine(worker_threads=8)
```

The trie RW lock allows unlimited concurrent readers. Writers (training) block all readers for the duration of the write. With `batch_insert`, write duration is minimised. In a read-heavy production scenario, `worker_threads=4–8` is appropriate.
