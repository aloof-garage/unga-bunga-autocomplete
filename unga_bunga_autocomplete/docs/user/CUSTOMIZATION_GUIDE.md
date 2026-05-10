# Customisation Guide

How to tailor UNGA BUNGA AUTO-COMPLETE to your preferences.

---

## Themes

Three built-in colour themes:

```powershell
python run.py --theme dark       # deep blue on black (default)
python run.py --theme light      # navy on white
python run.py --theme solarized  # Solarized palette
```

The theme affects:
- Ghost text colour
- Suggestion list colours
- Score highlight colours (green/yellow/red by score)
- Panel borders and headers

---

## Configuration File

All settings live in `~/.unga_bunga/config.json`. Edit it with any text editor.

On Windows: `C:\Users\YourName\.unga_bunga\config.json`

If the file does not exist yet, launch the engine once and it will be created.

---

## Engine Settings

```json
{
  "engine": {
    "max_suggestions": 10,
    "min_prefix_length": 1,
    "debounce_ms": 80,
    "fuzzy_threshold": 0.6,
    "fuzzy_max_distance": 3,
    "cache_size": 4096,
    "session_learning_enabled": true,
    "context_window_size": 5
  }
}
```

| Setting | Default | What it does |
|---------|---------|-------------|
| `max_suggestions` | 10 | Maximum suggestions shown |
| `min_prefix_length` | 1 | Minimum characters before suggestions appear |
| `debounce_ms` | 80 | Wait this long after a keystroke before querying (reduces flicker) |
| `fuzzy_threshold` | 0.6 | Minimum similarity score for fuzzy matches (0=anything, 1=exact only) |
| `fuzzy_max_distance` | 3 | Maximum typo distance for fuzzy search |
| `cache_size` | 4096 | How many recent query results to cache in memory |
| `session_learning_enabled` | true | Whether Tab-accepted suggestions improve future rankings |
| `context_window_size` | 5 | How many previous words to consider for context scoring |

---

## Ranking Weights

Control how much each signal influences the ranking:

```json
{
  "engine": {
    "weight_prefix":    3.0,
    "weight_session":   2.5,
    "weight_frequency": 2.0,
    "weight_context":   1.8,
    "weight_recency":   1.5,
    "weight_ngram":     1.2,
    "weight_fuzzy":     1.0
  }
}
```

**Higher number = stronger influence on ranking.**

### Tuning Examples

**I want the engine to strongly prefer recently used words:**
```json
"weight_recency": 4.0,
"weight_frequency": 1.0
```

**I want pure frequency ranking — most common words always win:**
```json
"weight_frequency": 5.0,
"weight_recency": 0.5,
"weight_session": 0.5
```

**I type in bursts and want session learning to dominate:**
```json
"weight_session": 5.0,
"weight_frequency": 1.0
```

**I want very strict prefix matching — no fuzzy suggestions:**
```json
"weight_fuzzy": 0.0,
"fuzzy_threshold": 0.99
```

---

## Training Settings

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
    "split_on_camel_case": true,
    "split_on_underscores": true
  }
}
```

| Setting | When to change |
|---------|---------------|
| `min_token_length` | Set to 3 to skip short words like "a", "is", "of" |
| `max_vocab_size` | Lower to 50,000 if memory is limited |
| `strip_numbers` | Set true if you don't want numbers as suggestions |
| `split_on_camel_case` | Set false if training on natural text (not code) |
| `lowercase` | Set false if you want case-sensitive completions |

---

## Persistence Settings

```json
{
  "persistence": {
    "data_dir": "~/.unga_bunga",
    "snapshot_interval_s": 300,
    "max_snapshots": 10,
    "autosave": true,
    "enable_wal_mode": true
  }
}
```

| Setting | What it does |
|---------|-------------|
| `data_dir` | Where to store the database and config |
| `snapshot_interval_s` | How often to auto-save (seconds). 300 = every 5 minutes |
| `max_snapshots` | How many backups to keep (oldest are deleted) |
| `autosave` | Set false to disable automatic saving |

### Change data directory

To store data on a different drive:
```json
{
  "persistence": {
    "data_dir": "D:\\autocomplete_data"
  }
}
```

---

## CLI / Shell Settings

```json
{
  "cli": {
    "history_max": 1000,
    "theme": "dark",
    "ghost_text": true,
    "show_scores": false,
    "prompt_symbol": "❯"
  }
}
```

Change the prompt symbol:
```json
"prompt_symbol": "→"
```

Disable ghost text permanently:
```json
"ghost_text": false
```

Always show scores:
```json
"show_scores": true
```

---

## Disable Persistence (Pure Memory Mode)

If you don't want anything written to disk:

```powershell
python run.py --no-persist
```

Or permanently in config:
```json
{
  "persistence": {
    "autosave": false
  }
}
```

In this mode, all training is lost when you close the shell.

---

## Reset Config to Defaults

Delete the config file — the engine recreates it with defaults on next launch:

```powershell
# Windows
del "%USERPROFILE%\.unga_bunga\config.json"

# Mac / Linux
rm ~/.unga_bunga/config.json
```
