# User Documentation

Everything you need to install, use, and customise UNGA BUNGA AUTO-COMPLETE.

---

## Contents

| Document | What it covers |
|----------|---------------|
| [../../README.md](../../README.md) | Installation, quick start, first steps |
| [TRAINING_GUIDE.md](TRAINING_GUIDE.md) | How to train the engine, corpus tips, best practices |
| [CUSTOMIZATION_GUIDE.md](CUSTOMIZATION_GUIDE.md) | Themes, config file, ranking weights, persistence options |
| [TROUBLESHOOTING.md](TROUBLESHOOTING.md) | Every known error with exact fixes |

---

## Quick Reference

### Start the shell
```powershell
cd unga_bunga_autocomplete_project
python run.py
```

### Train on a file
```powershell
python run.py --train myfile.txt
```

### Key bindings
| Key | Action |
|-----|--------|
| `Tab` | Accept suggestion |
| `Ctrl+N` | Next suggestion |
| `Ctrl+P` | Previous suggestion |
| `F1` | Toggle stats panel |
| `Ctrl+D` | Quit |

### Shell commands
| Command | Action |
|---------|--------|
| `:train <text or file>` | Train on text or file |
| `:stats` | Show engine statistics |
| `:reset` | Clear session learning |
| `:help` | Show help |
| `:quit` | Exit |

### Common fixes
| Problem | Fix |
|---------|-----|
| No suggestions | Run `:train <text>` first |
| ModuleNotFoundError | `cd ..` to the project root, then `python run.py` |
| Garbled characters | Use Windows Terminal, not classic CMD |
| Slow on 1-2 char prefixes | Normal — use 3+ characters |
