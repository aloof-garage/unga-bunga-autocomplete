# Troubleshooting

Exact fixes for every known error and problem.

---

## "No module named unga_bunga_autocomplete"

**Symptom:**
```
C:\...\unga_bunga_autocomplete> python -m unga_bunga_autocomplete
ModuleNotFoundError: No module named unga_bunga_autocomplete
```

**Cause:** You are running from inside the package folder instead of the project root.

**Fix:** Go up one level:
```powershell
# Your current wrong location:
C:\...\unga_bunga_autocomplete_project\unga_bunga_autocomplete>

# Correct location:
cd ..
# Now you are at:
C:\...\unga_bunga_autocomplete_project>

python run.py --version   # should print version now
```

**Rule:** Always run `python run.py` from `unga_bunga_autocomplete_project\`, never from the inner `unga_bunga_autocomplete\` subfolder.

---

## "No module named unga_bunga_autocomplete.core.lifecycle.persistence"

**Symptom:**
```
ModuleNotFoundError: No module named 'unga_bunga_autocomplete.core.lifecycle.persistence'
```

**Cause:** You have an old version of the files. This bug was fixed in the FIXED release.

**Fix:** Download the latest `unga_bunga_autocomplete_FIXED.zip` and extract fresh. Do not mix files from the original zip with the fixed one.

---

## "No suggestions" — Engine returns nothing

**Symptom:** You type a word and get no suggestions at all.

**Cause:** The engine starts with an empty vocabulary. It needs to be trained first.

**Fix:**
```
ub ❯ :train The quick brown fox jumps over the lazy dog
  ✓ Trained 9 tokens from inline text
```

Then try typing `qui` or `bro`. Or train on a larger file:
```
ub ❯ :train C:\path\to\my_notes.txt
```

---

## Suggestions are slow on short prefixes

**Symptom:** Typing 1 or 2 characters produces suggestions but takes a long time.

**Cause:** Very short prefixes match a large portion of the vocabulary. The engine must scan more nodes.

**Fix:** This is expected behaviour. Use prefixes of 3+ characters for fast, selective results. The engine is optimised for the 3–10 character range.

---

## Ghost text is not appearing

**Symptom:** No faded text appears after your cursor as you type.

**Causes and fixes:**

1. **Ghost text is disabled.** You launched with `--no-ghost`. Relaunch without it.

2. **Terminal does not support it.** Some older Windows terminals (classic `cmd.exe`) do not render the ghost text correctly. Use Windows Terminal or PowerShell 7+.

3. **No suggestions available.** Ghost text only shows when the top suggestion starts with your exact prefix. Train the engine first.

---

## Terminal shows garbled characters or boxes

**Symptom:** The shell shows `?` boxes or garbled output instead of Unicode characters.

**Cause:** Your terminal's font does not include the Unicode characters used by Rich.

**Fix (Windows):** Open Windows Terminal (not classic CMD). Set the font to "Cascadia Code" or "Consolas". In PowerShell:
```powershell
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
python run.py
```

**Fix (Mac/Linux):** Ensure `LANG` is set to a UTF-8 locale:
```bash
export LANG=en_US.UTF-8
python run.py
```

---

## "pip install" says "not writeable"

**Symptom:**
```
Defaulting to user installation because normal site-packages is not writeable
```

**Cause:** This is a warning, not an error. Python is installing to your user directory. Everything still works.

**Fix:** Nothing needed — ignore this warning. If you want to install system-wide, run PowerShell as Administrator.

---

## Database error on startup

**Symptom:**
```
Storage health check failed: *** in page 42 of main database
```

**Cause:** The SQLite database file is corrupt (rare — usually caused by a hard power cut during a write).

**Fix:** Delete the database and restart:
```powershell
# Windows
del "%USERPROFILE%\.unga_bunga\engine.db"

# Mac / Linux
rm ~/.unga_bunga/engine.db
```

The engine will rebuild from scratch. Previously trained vocabulary will be lost. To avoid this in future, the engine saves rolling snapshots — even if the latest is corrupt, older ones are tried automatically. True corruption of all 10 snapshots is extremely unlikely.

---

## Training file not found

**Symptom:**
```
[ERROR] Training file not found: path\to\file.txt
```

**Cause:** The path you typed does not exist.

**Fix:** Use the full absolute path:
```powershell
python run.py --train "C:\Users\me\Documents\corpus.txt"
```

Note the quotes around paths containing spaces.

---

## Engine stops responding (freezes)

**Symptom:** The shell stops responding to keypresses.

**Cause:** Extremely rare. Could be a deadlock in the thread pool or an unhandled exception in an async task.

**Fix:** Press `Ctrl+C` to interrupt. The engine will attempt a graceful shutdown (saves a snapshot first). If that fails, close the terminal window.

To investigate: relaunch with `--debug` and reproduce the freeze. The debug log will show which operation hung.

---

## "pytest-asyncio" version error

**Symptom:**
```
ERRORS: unga_bunga_autocomplete/tests/test_all.py - PytestUnraisableExceptionWarning
```
or
```
asyncio_mode not recognized
```

**Cause:** Old version of pytest-asyncio installed.

**Fix:**
```powershell
pip install --upgrade pytest-asyncio
```

Required version: 0.21 or newer.

---

## Tests fail with "111 passed" expected but fewer pass

**Cause:** You may have an old version of a source file. The FIXED zip is the only correct version.

**Fix:**
1. Extract the FIXED zip fresh into a clean directory.
2. Do not mix files from different zip versions.
3. Run `pytest unga_bunga_autocomplete/tests/ -v` from `unga_bunga_autocomplete_project\`.

---

## How to get more help

Run `:help` inside the shell for a command reference.

Use `--debug` for verbose engine logging:
```powershell
python run.py --debug
```

Check engine stats:
```powershell
python run.py --stats
```
