"""
UNGA BUNGA AUTO-COMPLETE — CLI Entry Point
==========================================

Usage (from the project root, next to run.py):
    python run.py                          ← recommended
    python -m unga_bunga_autocomplete      ← after pip install -e .

Options:
    --train FILE        Train on a text file before launching shell
    --train-text TEXT   Train on inline text before launching shell
    --theme THEME       Color theme: dark | light | solarized (default: dark)
    --no-persist        Disable persistence (in-memory only)
    --scores            Show score breakdown in suggestions
    --no-ghost          Disable ghost-text suggestions
    --version           Print version and exit
    --stats             Show engine stats and exit (requires existing DB)
    --benchmark         Run built-in benchmarks and exit
    --debug             Enable debug logging
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

# ── Path fix ──────────────────────────────────────────────────────────────────
# Ensure the project root (parent of this package) is on sys.path.
# This lets `python -m unga_bunga_autocomplete` work when run from the
# project root without a pip install.
_pkg_dir = Path(__file__).parent          # .../unga_bunga_autocomplete/
_project_root = _pkg_dir.parent           # .../unga_bunga_autocomplete_project/
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))
# ─────────────────────────────────────────────────────────────────────────────

import logging


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="unga_bunga_autocomplete",
        description="UNGA BUNGA AUTO-COMPLETE — Production-grade autocomplete platform",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--train", metavar="FILE", help="Train on text file before launching")
    p.add_argument("--train-text", metavar="TEXT", help="Train on inline text")
    p.add_argument("--theme", default="dark", choices=["dark", "light", "solarized"])
    p.add_argument("--no-persist", action="store_true", help="Disable persistence")
    p.add_argument("--scores", action="store_true", help="Show score breakdown")
    p.add_argument("--no-ghost", action="store_true", help="Disable ghost text")
    p.add_argument("--version", action="store_true", help="Print version")
    p.add_argument("--stats", action="store_true", help="Print stats and exit")
    p.add_argument("--benchmark", action="store_true", help="Run benchmarks")
    p.add_argument("--debug", action="store_true", help="Enable debug logging")
    return p


def setup_logging(debug: bool = False) -> None:
    level = logging.DEBUG if debug else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # Silence noisy third-party loggers
    for name in ("asyncio", "prompt_toolkit"):
        logging.getLogger(name).setLevel(logging.WARNING)


async def run_shell(args: argparse.Namespace) -> None:
    """Main async entry point."""
    from unga_bunga_autocomplete.core.lifecycle import LifecycleManager
    from unga_bunga_autocomplete.cli import Shell
    from unga_bunga_autocomplete.training import TrainingPipeline

    manager = LifecycleManager(
        enable_persistence=not args.no_persist,
    )

    async with manager:
        engine = manager.engine

        # Pre-training
        if args.train:
            path = Path(args.train)
            if not path.exists():
                print(f"[ERROR] Training file not found: {path}", file=sys.stderr)
                sys.exit(1)
            print(f"Training on {path}...")
            pipeline = TrainingPipeline()
            result = await pipeline.train_file(path, engine)
            print(
                f"  ✓ {result.token_count} tokens, {result.vocab_size} vocab, "
                f"{result.elapsed_s:.2f}s"
            )

        if args.train_text:
            count = await engine.train_text(args.train_text)
            print(f"  ✓ Trained on inline text ({count} tokens)")

        if args.stats:
            import json
            print(json.dumps(manager.stats(), indent=2))
            return

        # Launch shell
        shell = Shell(
            engine,
            theme=args.theme,
            show_scores=args.scores,
            show_ghost=not args.no_ghost,
        )
        await shell.run()


async def run_benchmark() -> None:
    """Run built-in benchmarks."""
    import time
    from unga_bunga_autocomplete.engine.trie import Trie
    from unga_bunga_autocomplete.engine.fuzzy import FuzzyEngine, similarity

    print("=" * 60)
    print("UNGA BUNGA AUTO-COMPLETE — Benchmarks")
    print("=" * 60)

    # ── Trie insert benchmark ─────────────────────────────────────────────
    print("\n[1/4] Trie batch insert — 100,000 words")
    trie = Trie()
    batch = [(f"benchmark_word_{i:07d}", i % 500 + 1) for i in range(100_000)]
    t0 = time.perf_counter()
    trie.batch_insert(batch)
    elapsed = time.perf_counter() - t0
    print(f"      {100_000 / elapsed:,.0f} words/sec  ({elapsed*1000:.1f}ms total)")

    # ── Trie prefix search benchmark ──────────────────────────────────────
    print("\n[2/4] Trie prefix search — 1,000 queries on 100k index")
    t0 = time.perf_counter()
    for i in range(1000):
        trie.search_prefix(f"benchmark_word_{i % 10:07d}"[:5], max_results=10)
    elapsed = time.perf_counter() - t0
    avg_ms = elapsed / 1000 * 1000
    print(f"      {1000/elapsed:,.0f} queries/sec  ({avg_ms:.3f}ms avg latency)")

    # ── Levenshtein benchmark ─────────────────────────────────────────────
    print("\n[3/4] Levenshtein — 50,000 pairs")
    from unga_bunga_autocomplete.engine.fuzzy import levenshtein
    pairs = [("hello", "helo"), ("python", "pyton"), ("world", "word"),
             ("benchmark", "benchmrk"), ("autocomplete", "autcomplete")] * 10_000
    t0 = time.perf_counter()
    for a, b in pairs:
        levenshtein(a, b)
    elapsed = time.perf_counter() - t0
    print(f"      {len(pairs)/elapsed:,.0f} pairs/sec  ({elapsed*1000:.1f}ms total)")

    # ── End-to-end suggestion benchmark ──────────────────────────────────
    print("\n[4/4] End-to-end suggestion engine — 500 queries")
    from unga_bunga_autocomplete.engine.suggestion_engine import SuggestionEngine
    engine = SuggestionEngine(debounce_ms=0)
    await engine.start()
    await engine.train_words([(f"word{i}", i % 100 + 1) for i in range(10_000)])

    t0 = time.perf_counter()
    for i in range(500):
        await engine.query_immediate(f"word{i % 50}")
    elapsed = time.perf_counter() - t0
    avg_ms = elapsed / 500 * 1000
    print(f"      {500/elapsed:,.0f} queries/sec  ({avg_ms:.3f}ms avg latency)")
    await engine.shutdown()

    # ── Serialisation benchmark ───────────────────────────────────────────
    print("\n[5/5] Trie serialisation — 100k words to JSON")
    t0 = time.perf_counter()
    json_data = trie.to_json()
    serialize_ms = (time.perf_counter() - t0) * 1000

    t0 = time.perf_counter()
    Trie.from_json(json_data)
    deserialize_ms = (time.perf_counter() - t0) * 1000

    print(f"      Serialize:   {serialize_ms:.1f}ms  ({len(json_data)/1024:.0f} KB)")
    print(f"      Deserialize: {deserialize_ms:.1f}ms")

    print("\n" + "=" * 60)
    print("Benchmarks complete.")
    print("=" * 60)


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    setup_logging(args.debug)

    if args.version:
        from unga_bunga_autocomplete import __version__
        print(f"UNGA BUNGA AUTO-COMPLETE v{__version__}")
        return

    if args.benchmark:
        asyncio.run(run_benchmark())
        return

    try:
        asyncio.run(run_shell(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
