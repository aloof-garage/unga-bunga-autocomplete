# UNGA BUNGA AUTO-COMPLETE — Quick Start Guide

## Installation

**Requirements**: Python 3.10+

```bash
# Clone the project
git clone https://github.com/your-org/unga-bunga-autocomplete
cd unga-bunga-autocomplete

# Install dependencies
pip install rich prompt_toolkit pytest pytest-asyncio

# Verify
python -m unga_bunga_autocomplete --version
# UNGA BUNGA AUTO-COMPLETE v1.0.0
```

## Your First Session

```bash
# Launch the interactive shell
python -m unga_bunga_autocomplete
```

You'll see:

```
╔══════════════════════════════════════════════════════╗
║         UNGA BUNGA AUTO-COMPLETE  v1.0.0             ║
║   Intelligent prefix completion • Type & explore     ║
║   Tab=accept  Ctrl+N/P=cycle  F1=stats  :help=help  ║
╚══════════════════════════════════════════════════════╝

Engine ready: 0 words loaded.
Type :help for commands.  Ctrl+D to quit.

ub ❯ 
```

## Training Your First Corpus

**Option A: Train on a text file**
```bash
python -m unga_bunga_autocomplete --train /path/to/my_text.txt
```

**Option B: Train inline during the session**
```
ub ❯ :train The quick brown fox jumps over the lazy dog
  ✓ Trained 9 tokens from inline text
```

**Option C: Train on a large corpus before launching**
```bash
python -m unga_bunga_autocomplete --train corpus/english_words.txt
Training on english_words.txt...
  ✓ 58,000 tokens, 45,000 vocab, 3.2s
```

## Autocomplete in Action

Once trained, start typing:

```
ub ❯ quic
  → quick          [0.82]    (prefix match, high frequency)
     quickly        [0.71]
     quicken        [0.65]
```

- **Tab** accepts the top suggestion
- **Ctrl+N** cycles to next suggestion  
- **Ctrl+P** cycles to previous suggestion
- **Escape** clears suggestions

## Ghost Text

Ghost text shows the top suggestion inline as dimmed text:

```
ub ❯ hel|lo  ← "lo" is ghost text, Tab to accept
```

Disable with `--no-ghost` flag.

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `Tab` | Accept top suggestion |
| `Ctrl+N` | Next suggestion |
| `Ctrl+P` | Previous suggestion |
| `Escape` | Clear suggestions |
| `F1` | Toggle diagnostics panel |
| `F2` | Toggle score display |
| `Ctrl+D` | Quit |
| `Ctrl+R` | Search command history |

## Shell Commands

| Command | Description |
|---------|-------------|
| `:help` | Show help |
| `:stats` | Show engine statistics |
| `:train <text or file>` | Train on text or file |
| `:reset` | Clear session learning |
| `:scores` | Toggle score breakdown |
| `:clear` | Clear screen |
| `:quit` | Exit |

## Themes

```bash
python -m unga_bunga_autocomplete --theme dark      # default
python -m unga_bunga_autocomplete --theme light
python -m unga_bunga_autocomplete --theme solarized
```

## Viewing Score Breakdowns

Enable with `--scores` or toggle with `F2`:

```
ub ❯ hel
  → hello  [0.81]    
     Score: prefix=0.8, frequency=0.40, recency=0.90, session=0.0
```

## Running Benchmarks

```bash
python -m unga_bunga_autocomplete --benchmark
```

Output:
```
[1/5] Trie batch insert — 100,000 words
      520,000 words/sec  (192ms total)
[2/5] Trie prefix search — 1,000 queries...
...
```

## Programmatic Usage

```python
import asyncio
from unga_bunga_autocomplete import create_engine

async def main():
    # Create and start engine
    engine = await create_engine(
        corpus_text="hello world help python programming"
    )

    # Query
    result = await engine.query_immediate("hel")
    for s in result.suggestions:
        print(f"{s.word:20s} score={s.final_score:.3f}")

    # Record selection (session learning)
    engine.record_selection("hello", context_tokens=["say"])

    await engine.shutdown()

asyncio.run(main())
```

## Data Location

All data is stored in `~/.unga_bunga/`:

```
~/.unga_bunga/
├── engine.db       # SQLite database (words, bigrams, snapshots)
├── config.json     # User configuration
└── cli_history     # Shell command history
```

## Troubleshooting

**"No suggestions"** — You need to train first. Run `:train <text>` or
start with `--train <file>`.

**Slow suggestions** — This happens with very short prefixes that match
a huge portion of the vocabulary. Use at least 3 characters.

**DB corruption** — Run with `--no-persist` to bypass. Delete
`~/.unga_bunga/engine.db` to reset.

**Import errors** — Install all dependencies:
`pip install rich prompt_toolkit`
