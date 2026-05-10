"""
UNGA BUNGA AUTO-COMPLETE — Application Lifecycle & Recovery
===========================================================
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class LifecycleManager:
    """
    Orchestrates startup, running, shutdown, and recovery.

    Usage::

        async with LifecycleManager() as manager:
            shell = Shell(manager.engine)
            await shell.run()
    """

    def __init__(
        self,
        data_dir: Optional[Path] = None,
        autosave_interval_s: int = 300,
        enable_persistence: bool = True,
    ) -> None:
        self._data_dir = data_dir or Path.home() / ".unga_bunga"
        self._autosave_interval = autosave_interval_s
        self._enable_persistence = enable_persistence
        self._engine = None
        self._storage = None
        self._autosave_task: Optional[asyncio.Task] = None
        self._started = False
        self._start_time: float = 0.0

    async def __aenter__(self) -> "LifecycleManager":
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.shutdown()
        return False

    async def start(self) -> None:
        if self._started:
            return
        self._start_time = time.monotonic()
        logger.info("=== UNGA BUNGA AUTO-COMPLETE starting ===")

        if self._enable_persistence:
            await self._init_storage()

        await self._init_engine()

        if self._enable_persistence and self._autosave_interval > 0:
            self._autosave_task = asyncio.create_task(
                self._autosave_loop(), name="autosave"
            )

        self._register_signals()
        self._started = True
        logger.info("Startup complete in %.2fs", time.monotonic() - self._start_time)

    async def _init_storage(self) -> None:
        # NOTE: absolute import — persistence lives at unga_bunga_autocomplete.persistence
        # NOT inside core.lifecycle
        from unga_bunga_autocomplete.persistence import Storage

        db_path = self._data_dir / "engine.db"
        self._storage = Storage(db_path, wal_mode=True)
        try:
            self._storage.open()
            health = self._storage.health_check()
            if not health["ok"]:
                logger.error("Storage health check failed: %s", health["details"])
        except Exception as exc:
            logger.error("Storage failed to open: %s — running without persistence", exc)
            self._storage = None

    async def _init_engine(self) -> None:
        # NOTE: absolute imports — engine lives at unga_bunga_autocomplete.engine
        from unga_bunga_autocomplete.engine.suggestion_engine import SuggestionEngine
        from unga_bunga_autocomplete.engine.trie import Trie

        trie = Trie()

        if self._storage is not None:
            snapshot_json = self._storage.load_latest_snapshot()
            if snapshot_json:
                try:
                    trie = Trie.from_json(snapshot_json)
                    logger.info("Trie restored: %d words", trie.word_count)
                except Exception as exc:
                    logger.error("Snapshot restore failed: %s — starting empty", exc)
                    trie = Trie()
            else:
                await self._load_words_from_db(trie)

        self._engine = SuggestionEngine(trie=trie)
        await self._engine.start()

        if self._storage is not None:
            await self._load_bigrams()

    async def _load_words_from_db(self, trie) -> None:
        if not self._storage:
            return
        loop = asyncio.get_event_loop()
        words = await loop.run_in_executor(None, self._storage.load_words)
        if words:
            trie.batch_insert([(w, f) for w, f, _ in words])
            logger.info("Loaded %d words from DB", len(words))

    async def _load_bigrams(self) -> None:
        if not self._storage or not self._engine:
            return
        loop = asyncio.get_event_loop()
        bigrams = await loop.run_in_executor(None, self._storage.load_bigrams, 500_000)
        scorer = self._engine.ranking.context_scorer
        for prev, nxt, count in bigrams:
            for _ in range(min(count, 3)):
                scorer.record_transition(prev, nxt)
        logger.info("Loaded %d bigram pairs", len(bigrams))

    async def shutdown(self) -> None:
        if not self._started:
            return
        logger.info("=== Shutting down UNGA BUNGA AUTO-COMPLETE ===")

        if self._autosave_task and not self._autosave_task.done():
            self._autosave_task.cancel()
            try:
                await self._autosave_task
            except asyncio.CancelledError:
                pass

        if self._storage and self._engine:
            await self._save_snapshot(label="shutdown")
            await self._flush_words_to_db()

        if self._engine:
            await self._engine.shutdown()

        if self._storage:
            self._storage.close()

        logger.info("Shutdown complete. Uptime: %.1fs", time.monotonic() - self._start_time)
        self._started = False

    async def _autosave_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(self._autosave_interval)
                await self._save_snapshot(label="autosave")
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Autosave error: %s", exc)

    async def _save_snapshot(self, label: str = "") -> None:
        if not self._storage or not self._engine:
            return
        try:
            loop = asyncio.get_event_loop()
            trie_json = await loop.run_in_executor(None, self._engine.trie.to_json)
            word_count = self._engine.trie.word_count
            await loop.run_in_executor(
                None, self._storage.create_snapshot, trie_json, word_count, label
            )
            logger.info("Snapshot saved (%s): %d words", label, word_count)
        except Exception as exc:
            logger.error("Snapshot save failed: %s", exc)

    async def _flush_words_to_db(self) -> None:
        if not self._storage or not self._engine:
            return
        try:
            loop = asyncio.get_event_loop()
            batch = [(r.word, r.frequency, "engine") for r in self._engine.trie.all_words()]
            if batch:
                await loop.run_in_executor(None, self._storage.upsert_words_batch, batch)
                logger.info("Flushed %d words to DB", len(batch))
        except Exception as exc:
            logger.error("Word flush failed: %s", exc)

    def _register_signals(self) -> None:
        """
        Register shutdown signal handlers.
        Windows: add_signal_handler() is not supported, fall back to signal.signal().
        POSIX:   use asyncio's add_signal_handler (loop-safe).
        """
        loop = asyncio.get_event_loop()

        if sys.platform == "win32":
            try:
                import signal as _sig
                _sig.signal(_sig.SIGINT, self._windows_signal_handler)
            except Exception:
                pass
            return

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(
                    sig,
                    lambda s=sig: asyncio.create_task(self._signal_shutdown(s)),
                )
            except (NotImplementedError, RuntimeError):
                pass

    def _windows_signal_handler(self, sig, frame) -> None:
        import threading
        threading.Thread(target=lambda: asyncio.run(self.shutdown()), daemon=True).start()

    async def _signal_shutdown(self, sig) -> None:
        logger.info("Signal received — shutting down")
        await self.shutdown()

    @property
    def engine(self):
        return self._engine

    @property
    def storage(self):
        return self._storage

    @property
    def is_running(self) -> bool:
        return self._started

    def stats(self) -> dict:
        s = {
            "uptime_s": round(time.monotonic() - self._start_time, 1),
            "persistence_enabled": self._enable_persistence,
            "data_dir": str(self._data_dir),
        }
        if self._engine:
            s["engine"] = self._engine.stats()
        if self._storage:
            s["storage"] = self._storage.stats()
        return s
