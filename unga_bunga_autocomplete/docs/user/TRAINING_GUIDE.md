# Training Guide

How to teach UNGA BUNGA AUTO-COMPLETE to know your vocabulary.

---

## The Basics

The engine starts with zero knowledge. You teach it by feeding it text. The more text you feed it, the better its suggestions become.

Training counts how often words appear. A word that appears 500 times in your corpus will rank much higher than one that appears twice.

---

## Method 1 — Train Before Launching

```powershell
python run.py --train path\to\myfile.txt
```

The engine trains on the file, then the interactive shell opens. Works for any plain `.txt` file.

---

## Method 2 — Train Inside the Shell

```
ub ❯ :train C:\Users\me\Documents\notes.txt
  ✓ Trained 28,400 tokens, vocab: 5,200 words, 1.8s

ub ❯ :train The quick brown fox jumps over the lazy dog
  ✓ Trained 9 tokens from inline text
```

You can run `:train` as many times as you like in a session. Each pass adds to the existing vocabulary — it does not erase previous training.

---

## Method 3 — Train Programmatically

```python
import asyncio
from pathlib import Path
from unga_bunga_autocomplete import create_engine
from unga_bunga_autocomplete.training import TrainingPipeline

async def main():
    engine = await create_engine()
    pipeline = TrainingPipeline()

    # Train on a file
    result = await pipeline.train_file(Path("corpus.txt"), engine)
    print(f"Trained: {result.token_count} tokens, vocab: {result.vocab_size}")

    # Train on another file — frequencies accumulate
    result2 = await pipeline.train_file(Path("more_corpus.txt"), engine)
    print(f"Added: {result2.token_count} tokens")

asyncio.run(main())
```

---

## What Makes a Good Corpus

The engine learns from whatever text you give it. Match the corpus to the kind of text you will be typing.

| Use Case | Good Corpus |
|----------|-------------|
| General writing | Books, articles, Wikipedia text |
| Code completion | Source code files, documentation |
| Personal notes | Your own previous documents and notes |
| Technical writing | Manuals, papers in your field |
| Chat / messaging | Previous chat logs (your own) |
| Domain-specific | Any text in that domain |

### Corpus Size Guidelines

| Corpus Size | Vocab | Quality |
|-------------|-------|---------|
| < 1,000 words | < 200 | Basic — only the words you typed |
| 10,000 words | ~1,000 | Decent — common words well-ranked |
| 100,000 words | ~5,000 | Good — most useful completions present |
| 1,000,000 words | ~30,000 | Excellent — rich, well-ranked vocabulary |

---

## Training on Multiple Files

Run `:train` multiple times — each pass adds to the vocabulary:

```
ub ❯ :train C:\corpus\chapter1.txt
  ✓ Trained 45,200 tokens, vocab: 8,300 words, 2.9s

ub ❯ :train C:\corpus\chapter2.txt
  ✓ Trained 41,800 tokens, vocab: 9,100 words, 2.7s
```

Or in code, call `train_file` multiple times:

```python
for path in Path("corpus/").glob("*.txt"):
    await pipeline.train_file(path, engine)
```

---

## What the Engine Learns

Beyond raw word frequencies, the engine also learns:

**Bigram transitions** — which words commonly follow other words. If "machine" is often followed by "learning" in your corpus, typing "machine l" will rank "learning" higher in context.

**Character n-grams** — allows fuzzy matching and typo correction even for words you have seen before.

---

## Session Learning (Automatic)

Even without explicit training, the engine learns within a session. Every time you press **Tab** to accept a suggestion, that word is reinforced:

- Its frequency in the trie increases by 1
- It gets a session-learning boost for the rest of the session
- The transition from the previous word to this word is recorded

This happens automatically — no configuration needed.

---

## Training Configuration

You can adjust how text is tokenised via `~/.unga_bunga/config.json`:

```json
{
  "training": {
    "min_token_length": 2,
    "max_token_length": 64,
    "max_vocab_size": 500000,
    "lowercase": true,
    "normalize_unicode": true,
    "strip_punctuation": true,
    "strip_numbers": false,
    "split_on_camel_case": true
  }
}
```

| Setting | Default | Effect |
|---------|---------|--------|
| `min_token_length` | 2 | Ignore single-character tokens |
| `max_vocab_size` | 500,000 | Cap total vocabulary size |
| `lowercase` | true | "Hello" and "hello" treated as the same word |
| `strip_numbers` | false | Set true to ignore purely numeric tokens |
| `split_on_camel_case` | true | "CamelCase" → ["Camel", "Case"] (useful for code) |

---

## Resetting the Vocabulary

To start fresh:

```
ub ❯ :reset
  ✓ Session reset
```

This clears only the in-session learning. To reset the full vocabulary:

```powershell
# Windows
del "%USERPROFILE%\.unga_bunga\engine.db"

# Mac / Linux
rm ~/.unga_bunga/engine.db
```

Then restart — the engine starts empty again.

---

## Tips for Best Results

1. **Train on text similar to what you will type.** A corpus of Shakespeare improves Shakespearean autocomplete, not Python code.

2. **More corpus = better.** 100,000 words produces noticeably richer completions than 1,000 words.

3. **Use `:train` on your own previous writing.** Your own documents contain your vocabulary and your patterns — this produces the most useful completions.

4. **Let session learning work.** Accept suggestions with Tab rather than typing them manually. Each accepted suggestion reinforces that word.

5. **Train incrementally.** You do not need to stop and restart. Run `:train` on new files any time.

6. **Context matters.** The engine tracks bigram transitions. Typing a common preceding word improves the ranking of its frequent successors.
