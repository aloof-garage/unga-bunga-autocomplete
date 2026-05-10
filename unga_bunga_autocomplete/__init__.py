"""
UNGA BUNGA AUTO-COMPLETE
========================
Production-grade autocomplete platform.

Quick start::

    # CLI mode
    python -m unga_bunga_autocomplete

    # With pre-loaded corpus
    python -m unga_bunga_autocomplete --train path/to/corpus.txt

    # Programmatic use
    from unga_bunga_autocomplete import create_engine
    import asyncio

    async def main():
        engine = await create_engine()
        await engine.train_text("hello world help")
        result = await engine.query_immediate("hel")
        for s in result.suggestions:
            print(s.word, s.final_score)

    asyncio.run(main())
"""

from __future__ import annotations

__version__ = "1.0.0"
__author__ = "UNGA BUNGA Team"

from .engine.suggestion_engine import SuggestionEngine
from .engine.trie import Trie, TrieResult
from .engine.fuzzy import FuzzyEngine, similarity
from .engine.ranking import RankingPipeline, RankedCandidate
from .training import TrainingPipeline
from .persistence import Storage
from .core.lifecycle import LifecycleManager


async def create_engine(
    data_dir=None,
    corpus_text: str = "",
    corpus_file=None,
) -> SuggestionEngine:
    """
    Convenience factory: create and start a SuggestionEngine.

    Optionally pre-trains on provided text or file.

    Args:
        data_dir:    Path to data directory for persistence.
        corpus_text: Text to train on before returning.
        corpus_file: Path to text file to train on.

    Returns:
        Running SuggestionEngine.
    """
    from pathlib import Path

    engine = SuggestionEngine()
    await engine.start()

    if corpus_text:
        await engine.train_text(corpus_text)

    if corpus_file:
        pipeline = TrainingPipeline()
        await pipeline.train_file(Path(corpus_file), engine)

    return engine


__all__ = [
    "SuggestionEngine",
    "Trie",
    "TrieResult",
    "FuzzyEngine",
    "similarity",
    "RankingPipeline",
    "RankedCandidate",
    "TrainingPipeline",
    "Storage",
    "LifecycleManager",
    "create_engine",
    "__version__",
]
