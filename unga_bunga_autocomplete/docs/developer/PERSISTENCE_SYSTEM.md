# Persistence System

File: `unga_bunga_autocomplete/persistence/__init__.py`

---

## Overview

The persistence layer uses a single SQLite database per installation with WAL mode enabled. It stores:

- Word frequencies and metadata (the vocabulary)
- Bigram transition counts (the context model)
- Trie snapshots as JSON blobs with SHA-256 checksums
- Schema version for migration tracking

Data flows:

```
Training corpus
      │
      ▼
FrequencyBuilder.feed()
      │
      ▼
SuggestionEngine.train_words()
      │
      ├──► Trie.batch_insert()   (in-memory, immediately queryable)
      │
      └──► LifecycleManager (on shutdown / autosave)
                │
                ├──► Storage.create_snapshot()   (JSON blob + checksum)
                └──► Storage.upsert_words_batch() (row-per-word backup)
```

---

## Schema

```sql
-- Vocabulary
CREATE TABLE words (
    word        TEXT    NOT NULL PRIMARY KEY,
    frequency   INTEGER NOT NULL DEFAULT 1,
    recency     REAL    NOT NULL DEFAULT 0,
    source      TEXT    NOT NULL DEFAULT 'user',   -- 'corpus' | 'user' | 'engine'
    created_at  REAL    NOT NULL DEFAULT (unixepoch('now', 'subsec'))
);

-- Context model
CREATE TABLE bigrams (
    prev_word   TEXT    NOT NULL,
    next_word   TEXT    NOT NULL,
    count       INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (prev_word, next_word)
);

-- Trie snapshots
CREATE TABLE snapshots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  REAL    NOT NULL,
    trie_json   TEXT    NOT NULL,
    word_count  INTEGER NOT NULL,
    checksum    TEXT    NOT NULL,   -- SHA-256 of trie_json
    label       TEXT    NOT NULL DEFAULT ''
);

-- Migration tracking
CREATE TABLE schema_version (
    version     INTEGER NOT NULL,
    applied     REAL    NOT NULL
);
```

Indexes on `words(frequency DESC)`, `words(recency DESC)`, `bigrams(prev_word)`, `snapshots(created_at DESC)`.

---

## WAL Mode

```sql
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
```

WAL (Write-Ahead Log) allows concurrent readers while a single writer is active. Without WAL, any write would lock the entire database, blocking all suggestion queries.

With WAL: reading threads see a consistent snapshot of the database and are never blocked by training writes.

---

## Thread Safety Model

Each thread gets its own SQLite connection via `threading.local()`:

```python
def _get_connection(self) -> sqlite3.Connection:
    if not hasattr(self._local, "conn") or self._local.conn is None:
        conn = sqlite3.connect(str(self._path), check_same_thread=False, timeout=30.0)
        self._local.conn = conn
    return self._local.conn
```

Write mutations are serialised through a `threading.Lock`:

```python
@contextmanager
def write_transaction(self) -> Generator[sqlite3.Connection, None, None]:
    with self._write_lock:       # exclusive write serialisation
        conn = self._get_connection()
        try:
            yield conn
            conn.commit()
        except Exception as exc:
            conn.rollback()
            raise
```

Reads use the thread-local connection directly (no lock needed in WAL mode).

---

## Snapshot Lifecycle

### Creating a Snapshot

```python
storage.create_snapshot(trie_json, word_count=45320, label="autosave")
```

1. Compute `checksum = SHA-256(trie_json)`
2. INSERT into snapshots table
3. Prune snapshots exceeding `max_snapshots` (default 10) by deleting oldest

All in a single write transaction.

### Loading a Snapshot

```python
trie_json = storage.load_latest_snapshot()
```

1. SELECT last 10 snapshots ordered by `created_at DESC`
2. For each: verify `SHA-256(trie_json) == stored checksum`
3. Return first that passes. Return `None` if all fail.

### Recovery Sequence

```
load_latest_snapshot()
  │
  ├─ Snapshot 1: checksum OK → return JSON
  │
  ├─ Snapshot 2: checksum FAIL → log warning, try next
  │
  ├─ Snapshot 3: checksum OK → return JSON
  │
  └─ All fail → return None → _load_words_from_db()
                                  │
                                  └─ SELECT words → batch_insert()
                                     (slower, no recency data)
```

---

## Migrations

Schema changes are tracked in the `schema_version` table. On startup, the current version is read and any pending migrations are applied in order.

### Current Migrations

| From | To | Change |
|------|----|--------|
| 0 | 1 | Initial schema |
| 1 | 2 | Added `source` column to `words` |
| 2 | 3 | Added `label` column to `snapshots` |

### Adding a Migration

```python
# In persistence/__init__.py

_SCHEMA_VERSION = 4  # increment

def _migration_v3_to_v4(conn: sqlite3.Connection) -> None:
    conn.execute("ALTER TABLE words ADD COLUMN tags TEXT")

_MIGRATIONS: List[Tuple[int, int, Any]] = [
    (1, 2, _migration_v1_to_v2),
    (2, 3, _migration_v2_to_v3),
    (3, 4, _migration_v3_to_v4),   # add here
]
```

Migrations run inside the `_run_migrations()` call during `Storage.open()`. Each migration is wrapped in the existing transaction from `_CREATE_SCHEMA`. If a migration fails, the transaction rolls back and an exception propagates.

---

## Atomic Writes

Trie JSON is written to a temporary file then renamed:

```python
tmp = path.with_suffix(".tmp")
tmp.write_text(self.to_json(), encoding="utf-8")
tmp.replace(path)   # atomic on POSIX, atomic on Windows (same drive)
```

If the process is killed between `write_text` and `replace`, the `.tmp` file is left behind and the original is untouched. On next startup, the `.tmp` file is ignored.

---

## Health Check

```python
result = storage.health_check()
# {"ok": True, "details": "ok"}
# {"ok": False, "details": "*** in page 42 of main database"}
```

Runs `PRAGMA integrity_check` on the SQLite file. Called on startup. If it fails, a warning is logged and the engine continues (degraded persistence).

---

## Batch Operations

All bulk operations use `executemany()` inside a single transaction:

```python
storage.upsert_words_batch([
    ("hello", 42, "corpus"),
    ("world", 17, "corpus"),
    ...  # 50,000 entries
])
```

One transaction for 50k rows = ~50ms. Per-row transactions = ~50 seconds. Never insert in a loop.

The SQL uses `ON CONFLICT DO UPDATE` (upsert) so re-training on the same corpus safely increments frequencies rather than duplicating rows.

---

## Storage Stats

```python
stats = storage.stats()
# {
#   "db_path": "/home/user/.unga_bunga/engine.db",
#   "word_count": 45320,
#   "snapshot_count": 7,
#   "db_size_mb": 12.4,
#   "schema_version": 3
# }
```

---

## Resetting the Database

```python
from pathlib import Path
Path("~/.unga_bunga/engine.db").expanduser().unlink(missing_ok=True)
```

Or from the shell:

```bash
# Linux / Mac
rm ~/.unga_bunga/engine.db

# Windows
del "%USERPROFILE%\.unga_bunga\engine.db"
```

The engine creates a fresh database on next startup.
