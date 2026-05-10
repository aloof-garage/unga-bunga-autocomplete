# Trie Engine Internals

File: `unga_bunga_autocomplete/engine/trie/__init__.py`

---

## What the Trie Does

The trie is the primary index for autocomplete. It provides O(k) prefix lookup where k is the length of the prefix — completely independent of vocabulary size.

A 500,000-word trie and a 100-word trie both answer "give me all words starting with 'hel'" in the same number of character steps: 3.

---

## Data Structure

### Node Layout

```python
class TrieNode:
    __slots__ = (
        "children",    # dict[str, TrieNode]   — character map to children
        "is_terminal", # bool                  — does a word end here?
        "word",        # str | None            — the full word (terminals only)
        "frequency",   # int                  — cumulative training count
        "recency",     # float                — monotonic timestamp
        "metadata",    # dict | None          — optional extra data
    )
```

`__slots__` eliminates the per-instance `__dict__`, saving ~160 bytes per node. At 200k nodes that is ~32 MB saved.

### Why a plain `dict` for children?

Options considered:

| Structure | Lookup | Insert | Memory | Chosen? |
|-----------|--------|--------|--------|---------|
| `dict[str, TrieNode]` | O(1) avg | O(1) avg | ~224 bytes empty | ✓ Yes |
| `list` + binary search | O(log a) | O(a) | Compact | No |
| Array of 128/256 | O(1) | O(1) | Always 1KB+ | No |

CPython's `dict` is extremely well-optimised for small keys. For natural language alphabets (~52 chars + punctuation), the dict is the right choice.

---

## Thread Safety: RW Lock

```python
class _RWLock:
    # Multiple concurrent readers allowed
    # Writers get exclusive access
    # Writer preference: pending writers block new readers
```

Read:write ratio in a live autocomplete session is roughly 100:1. The writer-preference policy prevents a steady stream of reads from starving a write (e.g. a bulk training insert).

```python
with trie._rwlock.read():   # shared — multiple threads can hold simultaneously
    ...

with trie._rwlock.write():  # exclusive — blocks all readers and other writers
    ...
```

---

## Insert

```python
def insert(self, word: str, frequency: int = 1, ...) -> None:
```

**Algorithm:**
1. Walk the trie character by character, creating nodes as needed
2. At the terminal node: if word exists, add to frequency; else create terminal

**Complexity:** O(k) where k = `len(word)`

**Frequency reinforcement:** Inserting the same word twice is intentional. Training passes over a corpus call `insert` for each occurrence, so frequency naturally accumulates.

---

## Batch Insert

```python
def batch_insert(self, words: List[Tuple[str, int]]) -> None:
```

Acquires the write lock **once** for the entire batch. For 100k words this is ~10× faster than per-word locking because lock acquisition has non-trivial overhead.

Use this for all bulk training. Use `insert()` only for single words (e.g. user-typed words).

---

## Prefix Search

```python
def search_prefix(self, prefix: str, max_results: int = 10) -> List[TrieResult]:
```

**Algorithm:**

```
1. Walk trie to prefix node — O(k)
   If any character is missing → return []

2. BFS from prefix node:
   stack = [prefix_node]
   heap  = []   (min-heap, size bounded by max_results)

   while stack:
       node = stack.pop()
       if node.is_terminal:
           score = _score(node)
           result = TrieResult(score=score, ...)
           if len(heap) < max_results:
               heappush(heap, result)
           elif result > heap[0]:          # new result beats worst in heap
               heapreplace(heap, result)
       stack.extend(node.children.values())

3. Sort heap descending, restore positive scores, return
```

**Complexity:** O(k + m log n)
- k = prefix length (navigation)
- m = number of terminal nodes in subtree
- n = max_results (heap size)

**Why min-heap?**
The heap tracks the top-n results. Its root is always the worst result in the current top-n. If a new result beats the root, we replace it. This gives us top-n without sorting all m results.

---

## Scoring

```python
@staticmethod
def _score(node: TrieNode) -> float:
    freq_score = math.log1p(node.frequency)
    age_s = max(0.0, time.monotonic() - node.recency)
    recency_factor = 1.0 / (1.0 + age_s / 3600.0)
    return freq_score * (1.0 + recency_factor)
```

This is the trie-internal score — a coarse ranking before the full ranking pipeline runs.

| Component | Effect |
|-----------|--------|
| `log1p(freq)` | Prevents hyper-frequent words from completely dominating |
| `recency_factor` | 2.0 when just inserted, smoothly decays toward 1.0 over hours |

The ranking pipeline applies further weights (prefix match, session, context) on top of these raw trie scores.

---

## Delete

```python
def delete(self, word: str) -> bool:
```

1. Walk to terminal node; return False if not found
2. Mark node as non-terminal (clears `word`, `frequency`, `recency`)
3. Bottom-up prune: remove leaf nodes that are now unreachable

**Why lazy pruning?** Eagerly restructuring the trie on every delete would require re-acquiring the write lock multiple times and is complex to implement safely. Background compaction is the production approach.

---

## Serialisation

```python
json_str = trie.to_json()     # → compact JSON list
trie2    = Trie.from_json(json_str)

trie.save(Path("trie.json"))
trie3 = Trie.load(Path("trie.json"))
```

**Format:** List of `[word, frequency, recency, metadata?]` arrays.

```json
[
  ["hello", 42, 1718000000.123],
  ["help",  17, 1718000001.456],
  ["café",   3, 1718000002.789, {"lang": "fr"}]
]
```

Storing words rather than raw nodes makes the format portable, human-readable, and easy to merge across machines.

**Atomic save:** Written to `.tmp` then `Path.replace()` — safe against interrupted writes.

**Corruption recovery:** `Trie.load()` returns an empty trie on any error rather than crashing. The persistence layer adds checksum verification on top.

---

## Reinforcement (Session Learning)

```python
trie.reinforce("hello", boost=1)
```

Increments a word's frequency and updates its recency timestamp. Called when the user accepts a suggestion. Boosts that word in future rankings without a full training pass.

---

## Complexity Summary

| Operation | Time | Notes |
|-----------|------|-------|
| `insert` | O(k) | k = word length |
| `batch_insert` | O(Σk) | Single write lock |
| `delete` | O(k) | Lazy node prune |
| `exact_match` | O(k) | Walk only |
| `starts_with` | O(k) | Walk only |
| `search_prefix` | O(k + m log n) | m = subtree size, n = max_results |
| `all_words` | O(n) | Generator, DFS |
| `to_json` | O(n) | n = word count |
| `from_json` | O(Σk) | Rebuild from scratch |

---

## Extending the Trie

**Add a field to nodes:**
1. Add to `TrieNode.__slots__` and `__init__`
2. Update `insert()` and `batch_insert()` to set it
3. Update `to_json()` / `from_json()` serialisation format
4. Increment `_SCHEMA_VERSION` in persistence if stored in DB

**Add a new traversal:**
Follow the pattern in `search_prefix` — explicit stack, never recurse (Python's default recursion limit is 1000, which a deep trie can exceed).

**Compress the trie (future):**
A Patricia trie merges single-child chains into edge labels. Implementation would replace `children: dict[str, TrieNode]` with `children: dict[str, (str, TrieNode)]` where the string is the edge label. Reduces node count by 60–80% for English text.
