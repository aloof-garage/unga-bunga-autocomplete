"""
UNGA BUNGA AUTO-COMPLETE — Persistence Layer
=============================================
SQLite-based persistence with ACID guarantees, WAL mode, atomic snapshots,
corruption detection, and a migration system.

Architecture:
    - Single SQLite database per installation
    - WAL mode: readers don't block writers; crash-safe
    - Atomic writes: all changes in explicit transactions
    - Trie serialised to JSON; stored as BLOB in DB + file snapshot
    - Snapshots: rolling set of N most-recent valid states
    - Migrations: version table + ordered migration functions

Thread safety:
    Each thread uses its own sqlite3 connection (check_same_thread=False +
    thread-local storage).  All mutations go through write_transaction()
    which serialises with a threading.Lock.

Recovery:
    On startup, checksum the latest snapshot; if corrupt, walk backwards
    through snapshots until a clean one is found.  If all fail, start empty
    with a warning.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Current schema version — increment when adding tables/columns
_SCHEMA_VERSION = 3


# ── Schema SQL ────────────────────────────────────────────────────────────────

_CREATE_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (
    version   INTEGER NOT NULL,
    applied   REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS words (
    word        TEXT    NOT NULL PRIMARY KEY,
    frequency   INTEGER NOT NULL DEFAULT 1,
    recency     REAL    NOT NULL DEFAULT 0,
    source      TEXT    NOT NULL DEFAULT 'user',
    created_at  REAL    NOT NULL DEFAULT (unixepoch('now', 'subsec'))
);

CREATE TABLE IF NOT EXISTS sessions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  REAL    NOT NULL,
    ended_at    REAL,
    word_count  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS session_words (
    session_id  INTEGER NOT NULL REFERENCES sessions(id),
    word        TEXT    NOT NULL,
    selections  INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (session_id, word)
);

CREATE TABLE IF NOT EXISTS bigrams (
    prev_word   TEXT    NOT NULL,
    next_word   TEXT    NOT NULL,
    count       INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (prev_word, next_word)
);

CREATE TABLE IF NOT EXISTS snapshots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  REAL    NOT NULL,
    trie_json   TEXT    NOT NULL,
    word_count  INTEGER NOT NULL,
    checksum    TEXT    NOT NULL,
    label       TEXT    NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_words_freq ON words(frequency DESC);
CREATE INDEX IF NOT EXISTS idx_words_recency ON words(recency DESC);
CREATE INDEX IF NOT EXISTS idx_bigrams_prev ON bigrams(prev_word);
CREATE INDEX IF NOT EXISTS idx_snapshots_created ON snapshots(created_at DESC);
"""


# ── Migrations ────────────────────────────────────────────────────────────────

def _migration_v1_to_v2(conn: sqlite3.Connection) -> None:
    """Add source column to words table."""
    conn.execute("ALTER TABLE words ADD COLUMN source TEXT NOT NULL DEFAULT 'corpus'")


def _migration_v2_to_v3(conn: sqlite3.Connection) -> None:
    """Add label column to snapshots table."""
    conn.execute("ALTER TABLE snapshots ADD COLUMN label TEXT NOT NULL DEFAULT ''")


_MIGRATIONS: List[Tuple[int, int, Any]] = [
    (1, 2, _migration_v1_to_v2),
    (2, 3, _migration_v2_to_v3),
]


# ── Checksum ──────────────────────────────────────────────────────────────────

def _checksum(data: str) -> str:
    """SHA-256 of UTF-8 encoded *data*."""
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


# ── Storage engine ────────────────────────────────────────────────────────────

class Storage:
    """
    SQLite-backed storage engine.

    Each Storage instance manages one database file.  Thread-local
    connections prevent cross-thread sqlite3 issues while still allowing
    concurrent reads via WAL mode.

    Example::

        storage = Storage(Path("~/.unga_bunga/engine.db"))
        storage.open()
        storage.upsert_word("hello", frequency=10)
        storage.create_snapshot(trie_json, word_count=1)
        storage.close()
    """

    def __init__(self, db_path: Path, wal_mode: bool = True) -> None:
        self._path = db_path
        self._wal = wal_mode
        self._write_lock = threading.Lock()
        self._local = threading.local()
        self._open = False

    # ── Connection management ─────────────────────────────────────────────

    def open(self) -> None:
        """Open the database, run migrations, initialise schema."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        conn = self._get_connection()

        if self._wal:
            conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")

        # Create schema
        conn.executescript(_CREATE_SCHEMA)

        # Check / set schema version
        self._run_migrations(conn)
        conn.commit()
        self._open = True
        logger.info("Storage opened: %s (WAL=%s)", self._path, self._wal)

    def close(self) -> None:
        """Close all thread-local connections and compact the DB."""
        if hasattr(self._local, "conn") and self._local.conn:
            try:
                self._local.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                self._local.conn.close()
            except sqlite3.Error:
                pass
            self._local.conn = None
        self._open = False
        logger.info("Storage closed")

    def _get_connection(self) -> sqlite3.Connection:
        """Return (or create) thread-local connection."""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            conn = sqlite3.connect(
                str(self._path),
                check_same_thread=False,
                timeout=30.0,
            )
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
        return self._local.conn

    @contextmanager
    def write_transaction(self) -> Generator[sqlite3.Connection, None, None]:
        """
        Context manager for exclusive write transactions.

        Acquires the write lock, yields the connection, commits on success,
        rolls back on exception.  Concurrent readers continue unblocked
        (WAL mode).

        Usage::

            with storage.write_transaction() as conn:
                conn.execute("INSERT INTO words ...")
        """
        with self._write_lock:
            conn = self._get_connection()
            try:
                yield conn
                conn.commit()
            except Exception as exc:
                conn.rollback()
                logger.error("Write transaction rolled back: %s", exc)
                raise

    def read(self, sql: str, params: tuple = ()) -> List[sqlite3.Row]:
        """Execute a SELECT and return all rows."""
        conn = self._get_connection()
        try:
            cur = conn.execute(sql, params)
            return cur.fetchall()
        except sqlite3.Error as exc:
            logger.error("Read error: %s — SQL: %s", exc, sql)
            return []

    # ── Migrations ────────────────────────────────────────────────────────

    def _run_migrations(self, conn: sqlite3.Connection) -> None:
        """Apply any pending schema migrations."""
        rows = conn.execute("SELECT version FROM schema_version ORDER BY version DESC LIMIT 1").fetchone()
        current_version = rows["version"] if rows else 0

        if current_version == 0:
            conn.execute(
                "INSERT INTO schema_version (version, applied) VALUES (?, ?)",
                (_SCHEMA_VERSION, time.time()),
            )
            logger.debug("Initialised schema at version %d", _SCHEMA_VERSION)
            return

        for from_v, to_v, fn in _MIGRATIONS:
            if current_version == from_v:
                logger.info("Running migration v%d → v%d", from_v, to_v)
                try:
                    fn(conn)
                    conn.execute(
                        "INSERT INTO schema_version (version, applied) VALUES (?, ?)",
                        (to_v, time.time()),
                    )
                    current_version = to_v
                except sqlite3.Error as exc:
                    logger.error("Migration v%d→v%d failed: %s", from_v, to_v, exc)
                    raise

    # ── Word operations ───────────────────────────────────────────────────

    def upsert_word(
        self,
        word: str,
        frequency: int = 1,
        recency: Optional[float] = None,
        source: str = "user",
    ) -> None:
        """Insert or update a word's frequency."""
        ts = recency or time.time()
        with self.write_transaction() as conn:
            conn.execute(
                """
                INSERT INTO words (word, frequency, recency, source)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(word) DO UPDATE SET
                    frequency = frequency + excluded.frequency,
                    recency = MAX(recency, excluded.recency)
                """,
                (word, frequency, ts, source),
            )

    def upsert_words_batch(self, words: List[Tuple[str, int, str]]) -> None:
        """
        Efficiently insert/update many words in a single transaction.

        Args:
            words: List of (word, frequency, source) triples.
        """
        ts = time.time()
        with self.write_transaction() as conn:
            conn.executemany(
                """
                INSERT INTO words (word, frequency, recency, source)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(word) DO UPDATE SET
                    frequency = frequency + excluded.frequency,
                    recency = MAX(recency, excluded.recency)
                """,
                [(w, f, ts, s) for w, f, s in words],
            )

    def load_words(self, limit: int = 500_000) -> List[Tuple[str, int, float]]:
        """
        Load words from DB for trie population.

        Returns:
            List of (word, frequency, recency).
        """
        rows = self.read(
            "SELECT word, frequency, recency FROM words ORDER BY frequency DESC LIMIT ?",
            (limit,),
        )
        return [(r["word"], r["frequency"], r["recency"]) for r in rows]

    # ── Bigrams ───────────────────────────────────────────────────────────

    def upsert_bigrams(self, bigrams: List[Tuple[str, str, int]]) -> None:
        """Batch-upsert bigram transition counts."""
        with self.write_transaction() as conn:
            conn.executemany(
                """
                INSERT INTO bigrams (prev_word, next_word, count)
                VALUES (?, ?, ?)
                ON CONFLICT(prev_word, next_word) DO UPDATE SET
                    count = count + excluded.count
                """,
                bigrams,
            )

    def load_bigrams(self, limit: int = 2_000_000) -> List[Tuple[str, str, int]]:
        """Load bigrams for context scorer initialisation."""
        rows = self.read(
            "SELECT prev_word, next_word, count FROM bigrams ORDER BY count DESC LIMIT ?",
            (limit,),
        )
        return [(r["prev_word"], r["next_word"], r["count"]) for r in rows]

    # ── Snapshots ─────────────────────────────────────────────────────────

    def create_snapshot(
        self,
        trie_json: str,
        word_count: int,
        label: str = "",
        max_snapshots: int = 10,
    ) -> int:
        """
        Store a trie snapshot and prune old ones.

        Args:
            trie_json:     JSON string from Trie.to_json().
            word_count:    Number of words in the trie (for quick validation).
            label:         Human-readable label (e.g. "pre-training").
            max_snapshots: Maximum number of snapshots to retain.

        Returns:
            snapshot_id

        Atomic: Uses write lock; truncates wal after write.
        """
        cksum = _checksum(trie_json)
        ts = time.time()

        with self.write_transaction() as conn:
            cur = conn.execute(
                "INSERT INTO snapshots (created_at, trie_json, word_count, checksum, label) "
                "VALUES (?, ?, ?, ?, ?)",
                (ts, trie_json, word_count, cksum, label),
            )
            snapshot_id = cur.lastrowid

            # Prune excess snapshots
            old = conn.execute(
                "SELECT id FROM snapshots ORDER BY created_at DESC LIMIT -1 OFFSET ?",
                (max_snapshots,),
            ).fetchall()
            if old:
                ids = tuple(r["id"] for r in old)
                conn.execute(
                    f"DELETE FROM snapshots WHERE id IN ({','.join('?' * len(ids))})",
                    ids,
                )

        logger.info(
            "Snapshot created: id=%d, words=%d, checksum=%s...",
            snapshot_id, word_count, cksum[:8],
        )
        return snapshot_id

    def load_latest_snapshot(self) -> Optional[str]:
        """
        Load the most-recent valid snapshot JSON.

        Validates checksum.  If invalid, tries the next-most-recent.
        Returns None if no valid snapshot found.

        Returns:
            Trie JSON string, or None.
        """
        rows = self.read(
            "SELECT id, trie_json, checksum, word_count FROM snapshots "
            "ORDER BY created_at DESC LIMIT 10"
        )
        for row in rows:
            trie_json = row["trie_json"]
            expected = row["checksum"]
            actual = _checksum(trie_json)
            if actual == expected:
                logger.info(
                    "Loaded snapshot id=%d, words=%d", row["id"], row["word_count"]
                )
                return trie_json
            else:
                logger.warning(
                    "Snapshot id=%d failed checksum (expected %s, got %s) — skipping",
                    row["id"], expected[:8], actual[:8],
                )

        logger.warning("No valid snapshot found — trie will start empty")
        return None

    def list_snapshots(self) -> List[Dict]:
        """Return metadata for all stored snapshots."""
        rows = self.read(
            "SELECT id, created_at, word_count, label, checksum FROM snapshots "
            "ORDER BY created_at DESC"
        )
        return [
            {
                "id": r["id"],
                "created_at": r["created_at"],
                "word_count": r["word_count"],
                "label": r["label"],
                "checksum_prefix": r["checksum"][:12],
            }
            for r in rows
        ]

    # ── Health check ──────────────────────────────────────────────────────

    def health_check(self) -> Dict[str, Any]:
        """
        Run SQLite integrity check.

        Returns:
            Dict with 'ok': bool and 'details': str.
        """
        try:
            rows = self.read("PRAGMA integrity_check")
            result = rows[0][0] if rows else "unknown"
            ok = result == "ok"
            if not ok:
                logger.error("DB integrity check FAILED: %s", result)
            return {"ok": ok, "details": result}
        except sqlite3.Error as exc:
            logger.error("Health check error: %s", exc)
            return {"ok": False, "details": str(exc)}

    # ── Stats ──────────────────────────────────────────────────────────────

    def stats(self) -> Dict[str, Any]:
        """Return storage statistics."""
        word_count_rows = self.read("SELECT COUNT(*) as n FROM words")
        snap_count_rows = self.read("SELECT COUNT(*) as n FROM snapshots")
        db_size = self._path.stat().st_size if self._path.exists() else 0

        return {
            "db_path": str(self._path),
            "word_count": word_count_rows[0]["n"] if word_count_rows else 0,
            "snapshot_count": snap_count_rows[0]["n"] if snap_count_rows else 0,
            "db_size_mb": round(db_size / 1_048_576, 2),
            "schema_version": _SCHEMA_VERSION,
        }
