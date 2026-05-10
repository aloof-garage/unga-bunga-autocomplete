# Developer Getting Started

Everything you need to go from zero to running tests and making your first change.

---

## Prerequisites

| Requirement | Minimum | Recommended |
|-------------|---------|-------------|
| Python | 3.10 | 3.12+ |
| OS | Windows 10 / macOS 12 / Ubuntu 20.04 | Any modern OS |
| RAM | 512 MB | 2 GB (for large corpus work) |
| Disk | 50 MB | 500 MB (for corpus files) |

---

## Clone and Set Up

```bash
git clone https://github.com/your-org/unga-bunga-autocomplete
cd unga_bunga_autocomplete_project

# Install runtime deps
pip install rich prompt_toolkit

# Install dev deps
pip install pytest pytest-asyncio coverage

# Verify
python run.py --version
# UNGA BUNGA AUTO-COMPLETE v1.0.0
```

---

## Run the Test Suite

```bash
# All 111 tests
pytest unga_bunga_autocomplete/tests/ -v

# A specific class
pytest unga_bunga_autocomplete/tests/ -v -k "TestTrie"

# With coverage
coverage run -m pytest unga_bunga_autocomplete/tests/
coverage report -m
```

---

## Run the Benchmarks

```bash
python run.py --benchmark
```

This runs 5 benchmarks covering trie insert, prefix search, Levenshtein, end-to-end queries, and serialisation. No mocking — real data.

---

## Development Workflow

```bash
# 1. Make your change
# 2. Run tests — must all pass before committing
pytest unga_bunga_autocomplete/tests/ -q

# 3. Run benchmarks if you touched engine code
python run.py --benchmark

# 4. Run the shell manually to sanity-check the TUI
python run.py --no-persist
```

---

## Project Entry Points

| File | Purpose |
|------|---------|
| `run.py` | Top-level launcher — adds project root to `sys.path` |
| `unga_bunga_autocomplete/__main__.py` | `python -m` entry point, argument parsing |
| `unga_bunga_autocomplete/__init__.py` | Public API surface, `create_engine()` factory |
| `unga_bunga_autocomplete/core/lifecycle/__init__.py` | Startup / shutdown sequence |

---

## Key Import Rule

All imports across the codebase use **absolute paths** from the package root:

```python
# CORRECT
from unga_bunga_autocomplete.engine.trie import Trie
from unga_bunga_autocomplete.persistence import Storage

# WRONG — relative imports across subpackage boundaries cause ModuleNotFoundError
from .engine.trie import Trie          # only works if caller is inside engine/
from ..persistence import Storage      # only works if caller is one level up
```

Relative imports (`.` / `..`) are only used within the same subpackage.

---

## Adding a New Module

1. Create the directory and `__init__.py`:
   ```
   unga_bunga_autocomplete/mymodule/__init__.py
   ```

2. Export from the package root if it is part of the public API (`__init__.py`).

3. Add tests in `tests/test_all.py` (or a new `tests/test_mymodule.py`).

4. Update `docs/developer/ARCHITECTURE.md` with the new module's role.

---

## Debug Mode

```bash
python run.py --debug
```

Sets the root logger to `DEBUG`. All subsystems log heavily: trie operations, cache hits/misses, query latencies, snapshot events.

For a single component:

```python
import logging
logging.getLogger("unga_bunga_autocomplete.engine.trie").setLevel(logging.DEBUG)
```
