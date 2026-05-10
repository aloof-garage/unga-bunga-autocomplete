# UNGA BUNGA AUTO-COMPLETE

> A production-grade autocomplete platform built entirely from scratch — custom trie, custom fuzzy engine, custom ranking pipeline, interactive TUI, SQLite persistence, and 111 passing tests.

---

## What is this?

UNGA BUNGA AUTO-COMPLETE is a self-contained intelligent text completion engine. You train it on any text, and it learns to suggest completions as you type — ranked by frequency, recency, session history, context, and fuzzy similarity.

It runs entirely in Python, uses zero external autocomplete libraries, and works offline.

```
ub ❯ pyth
  → python          [0.91]
     python3         [0.84]
     pythonic        [0.71]
```

---

## Quick Start — Windows

### 1. Extract the zip

Extract the zip anywhere. You will get a folder called `unga_bunga_autocomplete_project`.

### 2. Open PowerShell inside that folder

```powershell
cd unga_bunga_autocomplete_project
```

Your prompt must show:
```
C:\...\unga_bunga_autocomplete_project>
```

**Do NOT go into the inner `unga_bunga_autocomplete\` subfolder** — that is the Python package, not the launch directory.

### 3. Install dependencies

```powershell
pip install rich prompt_toolkit pytest pytest-asyncio
```

### 4. Launch

```powershell
python run.py
```

---

## Quick Start — Mac / Linux

```bash
unzip unga_bunga_autocomplete_FIXED.zip
cd unga_bunga_autocomplete_project
pip install rich prompt_toolkit pytest pytest-asyncio
python run.py
```

---

## First Steps Inside the Shell

The engine starts empty. Train it first:

```
ub ❯ :train The quick brown fox jumps over the lazy dog
  ✓ Trained 9 tokens from inline text
```

Now type any prefix:

```
ub ❯ qui
  → quick          [0.87]

ub ❯ br
  → brown           [0.82]
```

Press **Tab** to accept. **Ctrl+N** / **Ctrl+P** to cycle. **Ctrl+D** to quit.

---

## Training on Your Own Text

```powershell
# Train on a file before launching
python run.py --train path\to\corpus.txt

# Or train from inside the shell at any time using given file
ub ❯ :train words.txt
  ✓ Trained 42,800 tokens, vocab: 8,300 words, 3.1s

# Or train from inside the shell at any time use your own files
ub ❯ :train C:\Users\me\notes.txt
  ✓ Trained 42,800 tokens, vocab: 8,300 words, 3.1s
```

---

## All Launch Options

| Option | What it does |
|--------|-------------|
| `python run.py` | Launch interactive shell |
| `python run.py --train FILE` | Train on file, then launch |
| `python run.py --train-text "text"` | Train on inline text, then launch |
| `python run.py --theme dark` | Theme: `dark` / `light` / `solarized` |
| `python run.py --scores` | Show score breakdown per suggestion |
| `python run.py --no-ghost` | Disable inline ghost text |
| `python run.py --no-persist` | In-memory only, no disk storage |
| `python run.py --benchmark` | Run performance benchmarks |
| `python run.py --stats` | Print engine statistics and exit |
| `python run.py --debug` | Verbose debug logging |
| `python run.py --version` | Print version |

---

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `Tab` | Accept top suggestion |
| `Ctrl+N` | Next suggestion |
| `Ctrl+P` | Previous suggestion |
| `Escape` | Clear suggestions |
| `F1` | Toggle diagnostics panel |
| `F2` | Toggle score display |
| `Ctrl+R` | Search command history |
| `Ctrl+D` | Quit |

---

## Shell Commands

| Command | What it does |
|---------|-------------|
| `:train <text or path>` | Train on text or a `.txt` file |
| `:stats` | Show live engine stats |
| `:scores` | Toggle score breakdown |
| `:reset` | Clear session learning |
| `:clear` | Clear screen |
| `:help` | Show help panel |
| `:quit` | Exit |

---

## Score Display

Enable with `--scores` or toggle with `:scores`:

```
ub ❯ hel
  → hello     [0.81]   prefix=0.80  freq=0.40  recency=0.90  session=0.00
     help      [0.74]   prefix=0.88  freq=0.06  recency=0.90  session=0.00
```

---

## Diagnostics Panel (F1)

```
┌──────────────────┬───────────┐
│ Words in trie    │ 45,320    │
│ Estimated memory │ 56.8 MB   │
│ Cache hit rate   │ 73.4%     │
│ Total queries    │ 1,204     │
│ Avg latency      │ 0.72 ms   │
└──────────────────┴───────────┘
```

---

## Your Data

Everything is saved automatically in `~/.unga_bunga/`:

```
~/.unga_bunga/
├── engine.db     SQLite database (words, bigrams, snapshots)
├── config.json   Configuration
└── cli_history   Shell history
```

Auto-saves every 5 minutes and on clean exit. Recovers automatically from the last valid snapshot if anything goes wrong.

To reset completely:

```powershell
# Windows
rmdir /s "%USERPROFILE%\.unga_bunga"

# Mac / Linux
rm -rf ~/.unga_bunga
```

---

## Use as a Python Library

```python
import asyncio
from unga_bunga_autocomplete import create_engine

async def main():
    engine = await create_engine(
        corpus_text="hello world help python programming"
    )

    result = await engine.query_immediate("hel")
    for s in result.suggestions:
        print(f"{s.word:20s}  {s.final_score:.3f}")

    # Tell the engine which suggestion the user accepted
    engine.record_selection("hello", context_tokens=["say"])

    await engine.shutdown()

asyncio.run(main())
```

---

## Run the Tests

```powershell
pytest unga_bunga_autocomplete\tests\ -v
# 111 passed
```

---

## Install System-Wide (Optional)

```powershell
pip install -e .

# Now works from any folder:
python -m unga_bunga_autocomplete
python -m unga_bunga_autocomplete --benchmark
```

---

## Performance

| Operation | Speed |
|-----------|-------|
| Trie insert | ~450,000 words / sec |
| Suggestion query (10k vocab) | ~0.7 ms average |
| Fuzzy match (8-char strings) | ~50,000 pairs / sec |
| Serialize 100k-word trie | ~300 ms / 4.5 MB |

---

## Troubleshooting

**"No suggestions"** — Train first with `:train <text>` or `--train file.txt`.

**Slow on short prefixes** — Very short prefixes (1–2 chars) search a large subtree. Use 3+ characters.

**ModuleNotFoundError** — You are in the wrong directory. `cd` into `unga_bunga_autocomplete_project\`, not into the inner package folder.

**Corrupt database** — Delete `~/.unga_bunga/engine.db` and restart. Or run with `--no-persist`.

---

## Project Layout

```
unga_bunga_autocomplete_project\
├── run.py                    ← always launch from here
├── setup.py
├── README.md                 ← this file
└── unga_bunga_autocomplete\  ← package (do not run from here)
    ├── engine\               trie, fuzzy, ranking, suggestion engine
    ├── training\             corpus ingestion and tokenisation
    ├── persistence\          SQLite storage and snapshots
    ├── cli\                  interactive terminal shell
    ├── core\                 config, events, lifecycle manager
    ├── tests\                111 tests
    └── docs\                 developer and user documentation
```

---

MIT License
