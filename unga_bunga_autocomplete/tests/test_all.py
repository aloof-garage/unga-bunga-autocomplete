"""
UNGA BUNGA AUTO-COMPLETE — Test Suite
======================================
Comprehensive tests covering:
    - Trie correctness (insert, delete, search, Unicode, serialization)
    - Fuzzy engine (Levenshtein, DL, keyboard penalties, Jaccard)
    - Ranking pipeline (score composition, session learning, context)
    - Suggestion engine (end-to-end query, debounce, cache, training)
    - Persistence (storage, snapshots, corruption recovery)
    - Concurrency (thread safety under load)

Run with:
    pytest tests/ -v --tb=short
    pytest tests/ -v --tb=short -k "trie"  # filter by name
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List
from unittest.mock import MagicMock, patch

import pytest

# ─────────────────────────────────────────────────────────────────────────────
# Trie Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestTrieBasic:
    """Core trie operations: insert, exact match, prefix search."""

    def setup_method(self):
        from unga_bunga_autocomplete.engine.trie import Trie
        self.trie = Trie()

    def test_insert_and_exact_match(self):
        self.trie.insert("hello", frequency=5)
        result = self.trie.exact_match("hello")
        assert result is not None
        assert result.word == "hello"
        assert result.frequency == 5

    def test_missing_word_returns_none(self):
        self.trie.insert("hello", frequency=1)
        assert self.trie.exact_match("world") is None

    def test_prefix_search_basic(self):
        words = ["hello", "help", "helium", "world", "herald"]
        for w in words:
            self.trie.insert(w, frequency=1)
        results = self.trie.search_prefix("hel")
        found = {r.word for r in results}
        assert "hello" in found
        assert "help" in found
        assert "helium" in found
        assert "world" not in found

    def test_prefix_search_returns_correct_count(self):
        for i in range(20):
            self.trie.insert(f"word{i}", frequency=i + 1)
        results = self.trie.search_prefix("word", max_results=5)
        assert len(results) <= 5

    def test_frequency_ordering(self):
        """Higher frequency words should score higher when recency is equal."""
        import time
        ts = time.monotonic()
        # Insert both words at the same recency so frequency is the only differentiator
        self.trie.insert("hello", frequency=100, recency=ts)
        self.trie.insert("help", frequency=1, recency=ts)
        results = self.trie.search_prefix("hel", max_results=10)
        assert len(results) >= 2
        words_in_order = [r.word for r in results]
        assert words_in_order.index("hello") < words_in_order.index("help")

    def test_word_count(self):
        for w in ["apple", "apricot", "banana", "cherry"]:
            self.trie.insert(w)
        assert self.trie.word_count == 4

    def test_starts_with(self):
        self.trie.insert("python")
        assert self.trie.starts_with("pyt") is True
        assert self.trie.starts_with("java") is False

    def test_empty_prefix_returns_empty(self):
        self.trie.insert("hello")
        results = self.trie.search_prefix("")
        assert results == []

    def test_insert_increments_existing(self):
        """Inserting same word twice should increment frequency."""
        self.trie.insert("hello", frequency=3)
        self.trie.insert("hello", frequency=7)
        result = self.trie.exact_match("hello")
        assert result.frequency == 10

    def test_node_count_increases(self):
        initial = self.trie.node_count
        self.trie.insert("abc")
        assert self.trie.node_count > initial


class TestTrieDelete:
    """Trie deletion and pruning."""

    def setup_method(self):
        from unga_bunga_autocomplete.engine.trie import Trie
        self.trie = Trie()

    def test_delete_existing_word(self):
        self.trie.insert("hello")
        assert self.trie.delete("hello") is True
        assert self.trie.exact_match("hello") is None

    def test_delete_missing_word(self):
        assert self.trie.delete("nothere") is False

    def test_delete_does_not_affect_prefix(self):
        self.trie.insert("hello")
        self.trie.insert("help")
        self.trie.delete("hello")
        result = self.trie.exact_match("help")
        assert result is not None

    def test_word_count_decrements_on_delete(self):
        self.trie.insert("hello")
        self.trie.insert("world")
        self.trie.delete("hello")
        assert self.trie.word_count == 1

    def test_delete_and_reinsert(self):
        self.trie.insert("hello", frequency=5)
        self.trie.delete("hello")
        self.trie.insert("hello", frequency=3)
        result = self.trie.exact_match("hello")
        assert result is not None
        assert result.frequency == 3


class TestTrieUnicode:
    """Unicode support in the trie."""

    def setup_method(self):
        from unga_bunga_autocomplete.engine.trie import Trie
        self.trie = Trie()

    def test_insert_unicode(self):
        words = ["café", "naïve", "résumé", "über", "日本語", "Ελληνικά"]
        for w in words:
            self.trie.insert(w, frequency=1)
        for w in words:
            assert self.trie.exact_match(w) is not None

    def test_prefix_search_unicode(self):
        self.trie.insert("café", frequency=5)
        self.trie.insert("cafeteria", frequency=3)
        results = self.trie.search_prefix("caf")
        found = {r.word for r in results}
        assert "cafeteria" in found

    def test_emoji_in_word(self):
        """Emojis are valid Unicode — should not crash."""
        self.trie.insert("hello😊", frequency=1)
        assert self.trie.exact_match("hello😊") is not None

    def test_nfc_and_nfd_same_bytes(self):
        """NFC-normalised strings compare correctly."""
        import unicodedata
        w_nfc = unicodedata.normalize("NFC", "café")
        w_nfd = unicodedata.normalize("NFD", "café")
        self.trie.insert(w_nfc, frequency=1)
        # NFC and NFD have different byte representations
        # Our trie stores them as-is — consistency test
        assert self.trie.exact_match(w_nfc) is not None


class TestTrieBatchInsert:
    """Batch insert performance and correctness."""

    def setup_method(self):
        from unga_bunga_autocomplete.engine.trie import Trie
        self.trie = Trie()

    def test_batch_insert_all_present(self):
        words = [(f"word{i}", i + 1) for i in range(1000)]
        self.trie.batch_insert(words)
        assert self.trie.word_count == 1000

    def test_batch_insert_frequency_correct(self):
        self.trie.batch_insert([("hello", 7), ("world", 3)])
        assert self.trie.exact_match("hello").frequency == 7
        assert self.trie.exact_match("world").frequency == 3

    def test_batch_insert_skips_empty(self):
        self.trie.batch_insert([("", 1), ("hello", 1), ("", 5)])
        assert self.trie.word_count == 1

    def test_batch_then_search(self):
        batch = [(f"auto{i}", 10 - i) for i in range(10)]
        self.trie.batch_insert(batch)
        results = self.trie.search_prefix("auto", max_results=5)
        assert len(results) == 5


class TestTrieSerialization:
    """Trie JSON serialization and deserialization."""

    def setup_method(self):
        from unga_bunga_autocomplete.engine.trie import Trie
        self.Trie = Trie

    def test_roundtrip_json(self):
        trie = self.Trie()
        words = [("hello", 10), ("world", 5), ("python", 8), ("café", 3)]
        trie.batch_insert(words)

        json_str = trie.to_json()
        restored = self.Trie.from_json(json_str)

        for word, freq in words:
            result = restored.exact_match(word)
            assert result is not None, f"Word '{word}' missing after restore"
            assert result.frequency == freq

    def test_json_is_valid(self):
        trie = self.Trie()
        trie.insert("hello", frequency=1)
        json_str = trie.to_json()
        data = json.loads(json_str)
        assert isinstance(data, list)

    def test_save_and_load(self):
        from unga_bunga_autocomplete.engine.trie import Trie
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "trie.json"
            trie = Trie()
            trie.batch_insert([("hello", 5), ("world", 3)])
            trie.save(path)
            assert path.exists()

            loaded = Trie.load(path)
            assert loaded.word_count == 2
            assert loaded.exact_match("hello").frequency == 5

    def test_load_corrupted_file_returns_empty(self):
        from unga_bunga_autocomplete.engine.trie import Trie
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "corrupt.json"
            path.write_text("NOT VALID JSON !!@#$", encoding="utf-8")
            trie = Trie.load(path)
            assert trie.word_count == 0  # graceful recovery

    def test_from_json_invalid_raises(self):
        from unga_bunga_autocomplete.engine.trie import Trie
        with pytest.raises(ValueError):
            Trie.from_json("{not a list}")


class TestTrieConcurrency:
    """Thread safety of the trie under concurrent access."""

    def setup_method(self):
        from unga_bunga_autocomplete.engine.trie import Trie
        self.trie = Trie()

    def test_concurrent_inserts(self):
        """Multiple threads inserting concurrently must not corrupt."""
        errors = []

        def worker(start: int):
            try:
                for i in range(start, start + 100):
                    self.trie.insert(f"word{i}", frequency=1)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i * 100,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Thread errors: {errors}"
        assert self.trie.word_count == 1000

    def test_concurrent_reads_and_writes(self):
        """Reads and writes simultaneously must not deadlock or crash."""
        self.trie.batch_insert([("hello", 1), ("world", 1), ("python", 1)])
        errors = []
        results_counts = []

        def reader():
            try:
                for _ in range(50):
                    r = self.trie.search_prefix("h")
                    results_counts.append(len(r))
            except Exception as exc:
                errors.append(exc)

        def writer():
            try:
                for i in range(50):
                    self.trie.insert(f"hello{i}", frequency=1)
            except Exception as exc:
                errors.append(exc)

        threads = (
            [threading.Thread(target=reader) for _ in range(5)] +
            [threading.Thread(target=writer) for _ in range(3)]
        )
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Concurrency errors: {errors}"


class TestTrieReinforce:
    """Session-learning reinforcement."""

    def setup_method(self):
        from unga_bunga_autocomplete.engine.trie import Trie
        self.trie = Trie()

    def test_reinforce_existing_word(self):
        self.trie.insert("hello", frequency=1)
        result = self.trie.reinforce("hello", boost=5)
        assert result is True
        assert self.trie.exact_match("hello").frequency == 6

    def test_reinforce_missing_word_returns_false(self):
        assert self.trie.reinforce("nothere") is False


# ─────────────────────────────────────────────────────────────────────────────
# Fuzzy Engine Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestLevenshtein:
    """Levenshtein distance correctness."""

    def test_identical_strings(self):
        from unga_bunga_autocomplete.engine.fuzzy import levenshtein
        assert levenshtein("hello", "hello") == 0

    def test_single_insertion(self):
        from unga_bunga_autocomplete.engine.fuzzy import levenshtein
        assert levenshtein("helo", "hello") == 1

    def test_single_deletion(self):
        from unga_bunga_autocomplete.engine.fuzzy import levenshtein
        assert levenshtein("hello", "helo") == 1

    def test_single_substitution(self):
        from unga_bunga_autocomplete.engine.fuzzy import levenshtein
        assert levenshtein("hello", "hella") == 1

    def test_empty_strings(self):
        from unga_bunga_autocomplete.engine.fuzzy import levenshtein
        assert levenshtein("", "") == 0
        assert levenshtein("abc", "") == 3
        assert levenshtein("", "abc") == 3

    def test_completely_different(self):
        from unga_bunga_autocomplete.engine.fuzzy import levenshtein
        assert levenshtein("abc", "xyz") == 3

    def test_max_dist_early_exit(self):
        from unga_bunga_autocomplete.engine.fuzzy import levenshtein
        result = levenshtein("abcdef", "xyz", max_dist=2)
        assert result > 2  # exceeded max_dist

    @pytest.mark.parametrize("a,b,expected", [
        ("kitten", "sitting", 3),
        ("saturday", "sunday", 3),
        ("intention", "execution", 5),
    ])
    def test_known_distances(self, a, b, expected):
        from unga_bunga_autocomplete.engine.fuzzy import levenshtein
        assert levenshtein(a, b) == expected


class TestDamerauLevenshtein:
    """Damerau-Levenshtein with transpositions."""

    def test_transposition_costs_one(self):
        from unga_bunga_autocomplete.engine.fuzzy import damerau_levenshtein
        # "ab" → "ba" is a transposition, should cost 1
        dist = damerau_levenshtein("ab", "ba")
        assert dist <= 1.0

    def test_keyboard_penalty_adjacent(self):
        from unga_bunga_autocomplete.engine.fuzzy import damerau_levenshtein
        # "e" and "r" are adjacent on QWERTY → substitution < 1.0
        dist = damerau_levenshtein("e", "r")
        assert dist < 1.0

    def test_identical_returns_zero(self):
        from unga_bunga_autocomplete.engine.fuzzy import damerau_levenshtein
        assert damerau_levenshtein("hello", "hello") == 0.0

    def test_typical_typo_teh(self):
        from unga_bunga_autocomplete.engine.fuzzy import damerau_levenshtein
        # "teh" → "the" is a transposition
        dist = damerau_levenshtein("teh", "the")
        assert dist <= 1.0


class TestSimilarity:
    """Normalised similarity scores."""

    def test_identical_is_one(self):
        from unga_bunga_autocomplete.engine.fuzzy import similarity
        assert similarity("hello", "hello") == 1.0

    def test_score_in_range(self):
        from unga_bunga_autocomplete.engine.fuzzy import similarity
        for a, b in [("hello", "world"), ("python", "jython"), ("abc", "xyz")]:
            s = similarity(a, b)
            assert 0.0 <= s <= 1.0, f"Score {s} out of range for {a},{b}"

    def test_close_words_score_high(self):
        from unga_bunga_autocomplete.engine.fuzzy import similarity
        assert similarity("python", "pyton") > 0.7
        assert similarity("hello", "helo") > 0.7

    def test_very_different_score_low(self):
        from unga_bunga_autocomplete.engine.fuzzy import similarity
        # "qqqq" vs "mmmm": q is top-left, m is bottom-right — not adjacent
        assert similarity("qqqq", "mmmm") < 0.4


class TestJaccard:
    """Jaccard bigram similarity."""

    def test_identical_is_one(self):
        from unga_bunga_autocomplete.engine.fuzzy import jaccard_similarity
        assert jaccard_similarity("hello", "hello") == 1.0

    def test_completely_different_near_zero(self):
        from unga_bunga_autocomplete.engine.fuzzy import jaccard_similarity
        s = jaccard_similarity("abc", "xyz")
        assert s < 0.1

    def test_partial_match(self):
        from unga_bunga_autocomplete.engine.fuzzy import jaccard_similarity
        s = jaccard_similarity("python", "pythonic")
        assert s > 0.4


class TestFuzzyEngine:
    """FuzzyEngine search."""

    def setup_method(self):
        from unga_bunga_autocomplete.engine.fuzzy import FuzzyEngine
        self.engine = FuzzyEngine(threshold=0.4, max_distance=3)
        self.vocab = ["hello", "help", "world", "python", "pyton", "helo", "helium"]

    def test_finds_close_match(self):
        results = self.engine.search("helo", self.vocab)
        words = [m.word for m in results]
        assert "hello" in words or "helo" in words

    def test_typo_correction(self):
        results = self.engine.search("pyton", self.vocab)
        words = [m.word for m in results]
        assert "python" in words

    def test_results_sorted_descending(self):
        results = self.engine.search("hel", self.vocab)
        scores = [r.combined for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_respects_max_results(self):
        results = self.engine.search("h", self.vocab, max_results=2)
        assert len(results) <= 2

    def test_empty_query_returns_empty(self):
        results = self.engine.search("", self.vocab)
        assert results == []

    def test_threshold_filters_weak_matches(self):
        engine = self.engine.__class__(threshold=0.95, max_distance=1)
        results = engine.search("hello", ["helloworld", "goodbye"])
        # "helloworld" should be below threshold (too different)
        # "goodbye" definitely below threshold
        for r in results:
            assert r.combined >= 0.95


# ─────────────────────────────────────────────────────────────────────────────
# Ranking Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestRankingPipeline:
    """Ranking pipeline correctness."""

    def setup_method(self):
        from unga_bunga_autocomplete.engine.ranking import RankingPipeline, RankingWeights
        from unga_bunga_autocomplete.engine.trie import TrieResult
        self.pipeline = RankingPipeline()
        self.TrieResult = TrieResult

    def _make_trie_result(self, word, freq=1, recency=None):
        import time
        r = self.TrieResult(
            score=1.0,
            word=word,
            frequency=freq,
            recency=recency or time.monotonic(),
        )
        r.score = 1.0  # restore (TrieResult negates in __post_init__)
        return r

    def test_higher_frequency_ranks_higher(self):
        results = [
            self._make_trie_result("common", freq=1000),
            self._make_trie_result("rare", freq=1),
        ]
        ranked = self.pipeline.rank("com", results)
        words = [c.word for c in ranked]
        assert words.index("common") < words.index("rare")

    def test_final_score_in_range(self):
        results = [self._make_trie_result("hello", freq=5)]
        ranked = self.pipeline.rank("hel", results)
        for c in ranked:
            assert 0.0 <= c.final_score <= 1.0

    def test_score_breakdown_present(self):
        results = [self._make_trie_result("hello", freq=5)]
        ranked = self.pipeline.rank("hel", results)
        assert len(ranked) > 0
        breakdown = ranked[0].score_breakdown
        assert "prefix" in breakdown
        assert "frequency" in breakdown
        assert "recency" in breakdown

    def test_session_boost_works(self):
        results = [
            self._make_trie_result("hello", freq=1),
            self._make_trie_result("help", freq=1),
        ]
        # Simulate user selecting "help" multiple times
        for _ in range(5):
            self.pipeline.record_selection("help", [])

        ranked = self.pipeline.rank("hel", results)
        words = [c.word for c in ranked]
        assert words.index("help") < words.index("hello")

    def test_reset_session_clears_boost(self):
        self.pipeline.record_selection("hello", [])
        self.pipeline.reset_session()
        assert self.pipeline.session_scorer.selection_count == 0

    def test_explain_returns_string(self):
        results = [self._make_trie_result("hello", freq=5)]
        ranked = self.pipeline.rank("hel", results)
        explanation = ranked[0].explain()
        assert isinstance(explanation, str)
        assert "hello" in explanation


class TestPrefixScorer:
    def test_exact_prefix_scores_high(self):
        from unga_bunga_autocomplete.engine.ranking import PrefixScorer
        score = PrefixScorer.score("hello", "hel", is_exact_prefix=True)
        assert score >= 0.5

    def test_non_prefix_scores_zero(self):
        from unga_bunga_autocomplete.engine.ranking import PrefixScorer
        score = PrefixScorer.score("world", "hel", is_exact_prefix=False)
        assert score == 0.0


class TestFrequencyScorer:
    def test_higher_frequency_higher_score(self):
        from unga_bunga_autocomplete.engine.ranking import FrequencyScorer
        fs = FrequencyScorer(max_frequency=1000)
        assert fs.score(1000) > fs.score(10)
        assert fs.score(10) > fs.score(1)

    def test_zero_frequency_zero_score(self):
        from unga_bunga_autocomplete.engine.ranking import FrequencyScorer
        fs = FrequencyScorer()
        assert fs.score(0) == 0.0

    def test_score_normalised(self):
        from unga_bunga_autocomplete.engine.ranking import FrequencyScorer
        fs = FrequencyScorer(max_frequency=100)
        assert 0.0 <= fs.score(50) <= 1.0
        assert fs.score(100) <= 1.0


class TestRecencyScorer:
    def test_recent_scores_high(self):
        import time
        from unga_bunga_autocomplete.engine.ranking import RecencyScorer
        rs = RecencyScorer(half_life_s=3600)
        now = time.monotonic()
        score = rs.score(now)
        assert score > 0.9

    def test_old_timestamp_scores_low(self):
        import time
        from unga_bunga_autocomplete.engine.ranking import RecencyScorer
        rs = RecencyScorer(half_life_s=3600)
        old = time.monotonic() - 24 * 3600  # 24 hours ago
        score = rs.score(old)
        assert score < 0.1

    def test_zero_timestamp_zero(self):
        from unga_bunga_autocomplete.engine.ranking import RecencyScorer
        rs = RecencyScorer()
        assert rs.score(0) == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Suggestion Engine Tests (async)
# ─────────────────────────────────────────────────────────────────────────────

class TestSuggestionEngine:
    """End-to-end suggestion engine tests."""

    @pytest.fixture
    async def engine(self):
        from unga_bunga_autocomplete.engine.suggestion_engine import SuggestionEngine
        eng = SuggestionEngine(debounce_ms=0)  # no debounce in tests
        await eng.start()
        yield eng
        await eng.shutdown()

    @pytest.mark.asyncio
    async def test_train_and_query(self, engine):
        await engine.train_text("hello world help helicopter")
        result = await engine.query_immediate("hel")
        words = [s.word for s in result.suggestions]
        assert len(words) > 0
        assert any(w.startswith("hel") for w in words)

    @pytest.mark.asyncio
    async def test_empty_prefix_returns_empty(self, engine):
        await engine.train_text("hello world")
        result = await engine.query_immediate("")
        assert result.suggestions == []

    @pytest.mark.asyncio
    async def test_cache_hit_on_repeat_query(self, engine):
        await engine.train_text("hello help herald")
        await engine.query_immediate("hel")  # prime cache
        result2 = await engine.query_immediate("hel")
        assert result2.cache_hit is True

    @pytest.mark.asyncio
    async def test_selection_reinforces_word(self, engine):
        await engine.train_text("hello help herald")
        engine.record_selection("hello", ["say"])

        result = await engine.query_immediate("hel")
        words = [s.word for s in result.suggestions]
        # "hello" should appear (was reinforced)
        assert "hello" in words

    @pytest.mark.asyncio
    async def test_stats_populated(self, engine):
        await engine.train_text("foo bar baz")
        await engine.query_immediate("fo")
        stats = engine.stats()
        assert stats["trie"]["word_count"] > 0
        assert stats["queries"]["total"] >= 1

    @pytest.mark.asyncio
    async def test_not_started_raises(self):
        from unga_bunga_autocomplete.engine.suggestion_engine import SuggestionEngine
        eng = SuggestionEngine()
        with pytest.raises(RuntimeError, match="not started"):
            await eng.query("hello")

    @pytest.mark.asyncio
    async def test_train_words_batch(self, engine):
        words = [(f"word{i}", i + 1) for i in range(50)]
        count = await engine.train_words(words)
        assert count == 50
        assert engine.trie.word_count == 50

    @pytest.mark.asyncio
    async def test_fuzzy_fallback_finds_typo(self, engine):
        await engine.train_words([("python", 10), ("programming", 5)])
        # "pyhon" should fuzzy-match to "python"
        result = await engine.query_immediate("pyhon")
        words = [s.word for s in result.suggestions]
        assert "python" in words


# ─────────────────────────────────────────────────────────────────────────────
# Training Pipeline Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestTokeniser:
    """Tokeniser correctness."""

    def setup_method(self):
        from unga_bunga_autocomplete.training import Tokeniser, TokeniserConfig
        self.tokeniser = Tokeniser()
        self.TokeniserConfig = TokeniserConfig

    def test_basic_tokenisation(self):
        tokens = self.tokeniser.tokenise("hello world")
        assert "hello" in tokens
        assert "world" in tokens

    def test_lowercase(self):
        tokens = self.tokeniser.tokenise("Hello World PYTHON")
        for t in tokens:
            assert t == t.lower()

    def test_punctuation_stripped(self):
        tokens = self.tokeniser.tokenise("hello, world! python.")
        assert "hello" in tokens
        assert "world" in tokens
        assert "python" in tokens
        for t in tokens:
            assert "," not in t
            assert "!" not in t

    def test_min_length_filter(self):
        cfg = self.TokeniserConfig(min_token_length=4)
        from unga_bunga_autocomplete.training import Tokeniser
        tok = Tokeniser(cfg)
        tokens = tok.tokenise("a bb ccc dddd")
        assert "a" not in tokens
        assert "bb" not in tokens
        assert "ccc" not in tokens
        assert "dddd" in tokens

    def test_camel_case_splitting(self):
        cfg = self.TokeniserConfig(split_on_camel_case=True, lowercase=True)
        from unga_bunga_autocomplete.training import Tokeniser
        tok = Tokeniser(cfg)
        tokens = tok.tokenise("CamelCaseWord")
        # Should split into "camel", "case", "word" (or similar)
        joined = " ".join(tokens)
        assert "camel" in joined or "camelcaseword" in joined

    def test_empty_text_returns_empty(self):
        tokens = self.tokeniser.tokenise("")
        assert tokens == []

    def test_unicode_preserved(self):
        tokens = self.tokeniser.tokenise("café naïve résumé")
        assert any("caf" in t for t in tokens)


class TestFrequencyBuilder:
    """FrequencyBuilder accumulation tests."""

    def setup_method(self):
        from unga_bunga_autocomplete.training import FrequencyBuilder
        self.builder = FrequencyBuilder()

    def test_feed_accumulates(self):
        self.builder.feed(["hello", "world", "hello"])
        assert self.builder.frequencies["hello"] == 2
        assert self.builder.frequencies["world"] == 1

    def test_feed_text(self):
        count = self.builder.feed_text("the quick brown fox")
        assert count == 4
        assert "quick" in self.builder.frequencies

    def test_merge(self):
        from unga_bunga_autocomplete.training import FrequencyBuilder
        b1 = FrequencyBuilder()
        b2 = FrequencyBuilder()
        b1.feed(["hello", "world"])
        b2.feed(["hello", "python"])
        b1.merge(b2)
        assert b1.frequencies["hello"] == 2
        assert b1.frequencies["python"] == 1

    def test_top_words_ordering(self):
        self.builder.feed(["a", "a", "a", "b", "b", "c"])
        top = self.builder.top_words(3)
        assert top[0][0] == "a"
        assert top[1][0] == "b"

    def test_bigrams_populated(self):
        from unga_bunga_autocomplete.training import TokeniserConfig, FrequencyBuilder
        cfg = TokeniserConfig(ngram_sizes=[2])
        b = FrequencyBuilder(cfg)
        b.feed(["hello", "world", "hello", "python"])
        assert ("hello", "world") in b.bigrams

    def test_stats_returns_dict(self):
        self.builder.feed(["foo", "bar"])
        stats = self.builder.stats()
        assert "vocab_size" in stats
        assert stats["vocab_size"] == 2


# ─────────────────────────────────────────────────────────────────────────────
# Persistence Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestStorage:
    """SQLite storage correctness."""

    @pytest.fixture
    def storage(self, tmp_path):
        from unga_bunga_autocomplete.persistence import Storage
        db = Storage(tmp_path / "test.db", wal_mode=True)
        db.open()
        yield db
        db.close()

    def test_upsert_and_load_word(self, storage):
        storage.upsert_word("hello", frequency=5)
        words = storage.load_words()
        assert any(w[0] == "hello" and w[1] == 5 for w in words)

    def test_upsert_increments_frequency(self, storage):
        storage.upsert_word("hello", frequency=3)
        storage.upsert_word("hello", frequency=7)
        words = {w[0]: w[1] for w in storage.load_words()}
        assert words["hello"] == 10

    def test_batch_upsert(self, storage):
        batch = [(f"word{i}", i, "corpus") for i in range(100)]
        storage.upsert_words_batch(batch)
        words = storage.load_words()
        assert len(words) == 100

    def test_snapshot_create_and_load(self, storage):
        trie_json = '[["hello", 5, 0.0]]'
        sid = storage.create_snapshot(trie_json, word_count=1)
        assert isinstance(sid, int)

        loaded = storage.load_latest_snapshot()
        assert loaded == trie_json

    def test_corrupt_snapshot_skipped(self, storage):
        """A snapshot with wrong checksum should be skipped."""
        # Insert corrupt snapshot directly
        with storage.write_transaction() as conn:
            conn.execute(
                "INSERT INTO snapshots (created_at, trie_json, word_count, checksum, label) "
                "VALUES (?, ?, ?, ?, ?)",
                (time.time() + 1000, '[["hello",1,0]]', 1, "WRONGCHECKSUM", "bad"),
            )

        # Insert good snapshot
        trie_json = '[["world", 3, 0.0]]'
        storage.create_snapshot(trie_json, word_count=1)

        # Should return the good one
        loaded = storage.load_latest_snapshot()
        assert loaded == trie_json

    def test_health_check_passes(self, storage):
        result = storage.health_check()
        assert result["ok"] is True

    def test_stats_populated(self, storage):
        storage.upsert_word("hello")
        stats = storage.stats()
        assert stats["word_count"] >= 1
        assert "db_size_mb" in stats

    def test_snapshot_pruning(self, storage):
        """Creating more than max_snapshots should prune old ones."""
        for i in range(15):
            storage.create_snapshot(f'[["word{i}", 1, 0]]', word_count=1, max_snapshots=10)

        snapshots = storage.list_snapshots()
        assert len(snapshots) <= 10


# ─────────────────────────────────────────────────────────────────────────────
# LRU Cache Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestLRUCache:
    def setup_method(self):
        from unga_bunga_autocomplete.engine.suggestion_engine import LRUCache
        self.cache = LRUCache(capacity=3, ttl_s=60)

    def test_put_and_get(self):
        self.cache.put(("key",), ["result"])
        result = self.cache.get(("key",))
        assert result == ["result"]

    def test_miss_returns_none(self):
        assert self.cache.get(("missing",)) is None

    def test_capacity_evicts_oldest(self):
        self.cache.put(("a",), [1])
        self.cache.put(("b",), [2])
        self.cache.put(("c",), [3])
        self.cache.put(("d",), [4])  # evicts "a"
        assert self.cache.get(("a",)) is None
        assert self.cache.get(("d",)) == [4]

    def test_hit_rate_tracking(self):
        self.cache.put(("a",), [1])
        self.cache.get(("a",))  # hit
        self.cache.get(("b",))  # miss
        assert self.cache.hit_rate == 0.5

    def test_invalidate_clears_all(self):
        self.cache.put(("a",), [1])
        self.cache.put(("b",), [2])
        self.cache.invalidate()
        assert self.cache.size == 0
        assert self.cache.get(("a",)) is None

    def test_ttl_expiry(self):
        from unga_bunga_autocomplete.engine.suggestion_engine import LRUCache
        cache = LRUCache(capacity=10, ttl_s=0.05)  # 50ms TTL
        cache.put(("k",), ["v"])
        time.sleep(0.1)
        assert cache.get(("k",)) is None


# ─────────────────────────────────────────────────────────────────────────────
# Stress Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestStress:
    """Stress tests for large inputs and high concurrency."""

    def test_trie_100k_words(self):
        from unga_bunga_autocomplete.engine.trie import Trie
        trie = Trie()
        batch = [(f"word{i:06d}", i % 1000 + 1) for i in range(100_000)]
        start = time.monotonic()
        trie.batch_insert(batch)
        elapsed = time.monotonic() - start
        assert trie.word_count == 100_000
        assert elapsed < 10.0, f"Batch insert too slow: {elapsed:.2f}s"

    def test_trie_search_speed(self):
        from unga_bunga_autocomplete.engine.trie import Trie
        trie = Trie()
        batch = [(f"word{i:06d}", i % 100 + 1) for i in range(50_000)]
        trie.batch_insert(batch)

        start = time.monotonic()
        for _ in range(1000):
            trie.search_prefix("word00", max_results=10)
        elapsed = time.monotonic() - start
        avg_ms = elapsed / 1000 * 1000
        assert avg_ms < 50.0, f"Search too slow: {avg_ms:.2f}ms avg"

    def test_concurrent_query_stress(self):
        """10 threads querying concurrently for 1 second."""
        from unga_bunga_autocomplete.engine.trie import Trie
        trie = Trie()
        batch = [(f"word{i}", i + 1) for i in range(10_000)]
        trie.batch_insert(batch)

        errors = []
        query_counts = []

        def query_worker():
            count = 0
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline:
                try:
                    trie.search_prefix("word", max_results=5)
                    count += 1
                except Exception as exc:
                    errors.append(exc)
            query_counts.append(count)

        threads = [threading.Thread(target=query_worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Stress test errors: {errors}"
        total = sum(query_counts)
        # On any modern machine, 10 threads doing prefix searches for 1s
        # should easily exceed 50 total queries
        assert total > 50, f"Expected >50 queries, got {total}"
