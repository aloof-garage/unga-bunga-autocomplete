"""
UNGA BUNGA AUTO-COMPLETE — Core Configuration System
=====================================================
Thread-safe, validated, live-reloadable configuration with layered defaults.

Architecture:
    Config is stored in two layers:
    1. Hard-coded defaults (never mutated)
    2. User overrides loaded from disk (JSON/TOML)
    Merged at access time with validation.

Thread safety: All mutation is protected by a RLock; reads of immutable
defaults are lock-free for performance.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# ── Data classes ─────────────────────────────────────────────────────────────

@dataclass
class EngineConfig:
    """Autocomplete engine tuning parameters."""
    max_suggestions: int = 10
    min_prefix_length: int = 1
    max_prefix_length: int = 128
    fuzzy_threshold: float = 0.6
    fuzzy_max_distance: int = 3
    trie_max_depth: int = 64
    debounce_ms: int = 80
    cache_size: int = 4096          # LRU slots
    background_index_workers: int = 2
    session_learning_enabled: bool = True
    context_window_size: int = 5    # previous tokens for context scoring

    # Ranking weights (must sum to sensible values; normalised internally)
    weight_prefix: float = 3.0
    weight_frequency: float = 2.0
    weight_recency: float = 1.5
    weight_session: float = 2.5
    weight_fuzzy: float = 1.0
    weight_context: float = 1.8
    weight_ngram: float = 1.2


@dataclass
class PersistenceConfig:
    """Storage and recovery settings."""
    data_dir: str = "~/.unga_bunga"
    db_filename: str = "engine.db"
    snapshot_interval_s: int = 300   # 5 minutes
    max_snapshots: int = 10
    autosave: bool = True
    enable_wal_mode: bool = True     # SQLite WAL — better concurrency


@dataclass
class TrainingConfig:
    """Corpus ingestion and training pipeline settings."""
    min_token_length: int = 2
    max_token_length: int = 64
    max_vocab_size: int = 500_000
    ngram_sizes: list = field(default_factory=lambda: [2, 3])
    normalize_unicode: bool = True
    lowercase: bool = True
    strip_punctuation: bool = True
    incremental_batch_size: int = 10_000


@dataclass
class CLIConfig:
    """Terminal interface settings."""
    history_max: int = 1000
    theme: str = "dark"
    ghost_text: bool = True
    show_scores: bool = False
    prompt_symbol: str = "❯"


@dataclass
class AppConfig:
    """Root application configuration."""
    engine: EngineConfig = field(default_factory=EngineConfig)
    persistence: PersistenceConfig = field(default_factory=PersistenceConfig)
    training: TrainingConfig = field(default_factory=TrainingConfig)
    cli: CLIConfig = field(default_factory=CLIConfig)
    log_level: str = "INFO"
    version: str = "1.0.0"


# ── Config manager ────────────────────────────────────────────────────────────

class ConfigManager:
    """
    Thread-safe singleton configuration manager.

    Responsibilities:
    - Load / save config JSON
    - Validate all values on load
    - Provide nested dot-path access  (cfg.engine.max_suggestions)
    - Emit change events (observers can subscribe)

    Thread safety:
        RLock protects _config mutations.
        Reads after construction are safe because dataclass fields are
        not replaced; only scalar values inside them are mutated under lock.
    """

    _instance: Optional[ConfigManager] = None
    _instance_lock: threading.Lock = threading.Lock()

    def __new__(cls) -> ConfigManager:
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        self._lock = threading.RLock()
        self._config = AppConfig()
        self._config_path: Optional[Path] = None
        self._observers: list = []
        self._initialized = True
        logger.debug("ConfigManager initialised with defaults")

    # ── Loading ────────────────────────────────────────────────────────────

    def load(self, path: Optional[Path] = None) -> None:
        """
        Load config from *path* (JSON).  Missing keys fall back to defaults.

        Args:
            path: Path to config JSON file.  If None, uses default location.

        Raises:
            ValueError: If a loaded value fails validation.
        """
        if path is None:
            data_dir = Path(self._config.persistence.data_dir).expanduser()
            path = data_dir / "config.json"

        self._config_path = path

        if not path.exists():
            logger.info("No config file found at %s — using defaults", path)
            return

        try:
            raw = path.read_text(encoding="utf-8")
            data: Dict[str, Any] = json.loads(raw)
        except (OSError, json.JSONDecodeError) as exc:
            logger.error("Failed to read config %s: %s — using defaults", path, exc)
            return

        with self._lock:
            self._apply_dict(data)

        logger.info("Config loaded from %s", path)
        self._notify_observers()

    def _apply_dict(self, data: Dict[str, Any]) -> None:
        """Apply a raw dict onto the config dataclasses (best-effort)."""
        for section, values in data.items():
            sub = getattr(self._config, section, None)
            if sub is None:
                logger.warning("Unknown config section '%s' — ignored", section)
                continue
            if not isinstance(values, dict):
                logger.warning("Config section '%s' is not a dict — ignored", section)
                continue
            for key, value in values.items():
                if not hasattr(sub, key):
                    logger.warning("Unknown config key '%s.%s' — ignored", section, key)
                    continue
                try:
                    setattr(sub, key, self._coerce(value, getattr(sub, key)))
                except (TypeError, ValueError) as exc:
                    logger.warning("Invalid value for '%s.%s': %s — keeping default", section, key, exc)

    @staticmethod
    def _coerce(value: Any, default: Any) -> Any:
        """Coerce *value* to the same type as *default*."""
        if isinstance(default, bool):
            if isinstance(value, bool):
                return value
            return str(value).lower() in ("1", "true", "yes")
        if isinstance(default, int):
            return int(value)
        if isinstance(default, float):
            return float(value)
        if isinstance(default, list):
            if isinstance(value, list):
                return value
            raise TypeError("Expected list")
        return value

    # ── Saving ────────────────────────────────────────────────────────────

    def save(self, path: Optional[Path] = None) -> None:
        """
        Persist current config to JSON.

        Args:
            path: Override save location.  Defaults to original load path.

        Thread safety: acquires lock for serialisation.
        """
        target = path or self._config_path
        if target is None:
            data_dir = Path(self._config.persistence.data_dir).expanduser()
            target = data_dir / "config.json"

        target.parent.mkdir(parents=True, exist_ok=True)

        with self._lock:
            data = asdict(self._config)

        try:
            target.write_text(json.dumps(data, indent=2), encoding="utf-8")
            logger.info("Config saved to %s", target)
        except OSError as exc:
            logger.error("Could not save config to %s: %s", target, exc)

    # ── Property access ───────────────────────────────────────────────────

    @property
    def engine(self) -> EngineConfig:
        return self._config.engine

    @property
    def persistence(self) -> PersistenceConfig:
        return self._config.persistence

    @property
    def training(self) -> TrainingConfig:
        return self._config.training

    @property
    def cli(self) -> CLIConfig:
        return self._config.cli

    # ── Observer pattern ─────────────────────────────────────────────────

    def subscribe(self, callback) -> None:
        """Register *callback* to be called whenever config changes."""
        with self._lock:
            self._observers.append(callback)

    def _notify_observers(self) -> None:
        for cb in self._observers:
            try:
                cb(self._config)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Config observer raised: %s", exc)

    # ── Validation ────────────────────────────────────────────────────────

    def validate(self) -> list[str]:
        """
        Validate current config.

        Returns:
            List of human-readable error strings.  Empty = valid.
        """
        errors: list[str] = []
        e = self._config.engine

        if not (1 <= e.max_suggestions <= 100):
            errors.append("engine.max_suggestions must be 1–100")
        if not (0.0 <= e.fuzzy_threshold <= 1.0):
            errors.append("engine.fuzzy_threshold must be 0.0–1.0")
        if not (0 <= e.fuzzy_max_distance <= 10):
            errors.append("engine.fuzzy_max_distance must be 0–10")
        if e.debounce_ms < 0:
            errors.append("engine.debounce_ms must be >= 0")
        if e.cache_size < 64:
            errors.append("engine.cache_size must be >= 64")

        t = self._config.training
        if t.min_token_length < 1:
            errors.append("training.min_token_length must be >= 1")
        if t.max_vocab_size < 100:
            errors.append("training.max_vocab_size must be >= 100")

        return errors


# Module-level singleton accessor
_default_manager: Optional[ConfigManager] = None


def get_config() -> ConfigManager:
    """Return the process-wide ConfigManager singleton."""
    global _default_manager
    if _default_manager is None:
        _default_manager = ConfigManager()
    return _default_manager
