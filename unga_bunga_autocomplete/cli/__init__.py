"""
UNGA BUNGA AUTO-COMPLETE — CLI / TUI Shell
==========================================
A premium interactive terminal experience powered by prompt_toolkit and rich.

Features:
    - Live inline suggestions as you type (ghost text)
    - Suggestion popup with score breakdown
    - Command history with fuzzy search
    - Diagnostics / stats panel
    - Training commands
    - Theme support (dark/light/solarized)
    - Keyboard-first design

Architecture:
    The shell owns an asyncio event loop and runs the SuggestionEngine inside it.
    prompt_toolkit provides the readline-style input with custom key bindings.
    Rich handles all formatted output.

    Key binding diagram:
        Tab       → accept ghost/first suggestion
        Ctrl+N/P  → cycle through suggestions
        Ctrl+D    → quit
        Ctrl+R    → reverse history search
        F1        → toggle diagnostics panel
        F2        → toggle score breakdown
        :train    → train on text/file
        :stats    → show engine stats
        :reset    → clear session
        :snapshot → create snapshot
        :help     → show help
"""

from __future__ import annotations

import asyncio
import logging
import sys
import time
from pathlib import Path
from typing import List, Optional, Sequence

logger = logging.getLogger(__name__)

try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.auto_suggest import AutoSuggest, Suggestion
    from prompt_toolkit.buffer import Buffer
    from prompt_toolkit.completion import Completer, Completion
    from prompt_toolkit.document import Document
    from prompt_toolkit.formatted_text import HTML, StyleAndTextTuples
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.styles import Style
    HAS_PROMPT_TOOLKIT = True
except ImportError:
    HAS_PROMPT_TOOLKIT = False
    logger.warning("prompt_toolkit not available; CLI will use basic input")

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    from rich.columns import Columns
    HAS_RICH = True
except ImportError:
    HAS_RICH = False

# ── Themes ────────────────────────────────────────────────────────────────────

THEMES = {
    "dark": {
        "ghost": "#555555",
        "suggestion_selected": "#00AFFF bold",
        "suggestion_normal": "#888888",
        "score_high": "#00FF87",
        "score_mid": "#FFD700",
        "score_low": "#FF6347",
        "prompt": "#00AFFF bold",
        "border": "#333333",
        "header": "#00AFFF",
    },
    "light": {
        "ghost": "#AAAAAA",
        "suggestion_selected": "#0066CC bold",
        "suggestion_normal": "#444444",
        "score_high": "#006400",
        "score_mid": "#8B6914",
        "score_low": "#8B0000",
        "prompt": "#0066CC bold",
        "border": "#CCCCCC",
        "header": "#0066CC",
    },
    "solarized": {
        "ghost": "#586e75",
        "suggestion_selected": "#268bd2 bold",
        "suggestion_normal": "#657b83",
        "score_high": "#859900",
        "score_mid": "#b58900",
        "score_low": "#dc322f",
        "prompt": "#268bd2 bold",
        "border": "#073642",
        "header": "#268bd2",
    },
}


# ── Ghost-text auto-suggest ───────────────────────────────────────────────────

if HAS_PROMPT_TOOLKIT:

    class EngineAutoSuggest(AutoSuggest):
        """
        Provides ghost-text (inline) suggestions from the SuggestionEngine.

        The ghost text shows the top-ranked suggestion as dimmed text
        extending the current input.  Tab accepts it.
        """

        def __init__(self, shell: Shell) -> None:
            self._shell = shell

        def get_suggestion(
            self, buffer: Buffer, document: Document
        ) -> Optional[Suggestion]:
            prefix = document.text_before_cursor
            if not prefix.strip():
                return None

            # Use cached suggestions
            suggestions = self._shell._last_suggestions
            if not suggestions:
                return None

            top = suggestions[0]
            if top.word.lower().startswith(prefix.lower()):
                # Suggest the remainder
                return Suggestion(top.word[len(prefix):])
            return None

    class EngineCompleter(Completer):
        """
        Dropdown completion menu with scores.

        Shows the top-N suggestions with their final scores.
        """

        def __init__(self, shell: Shell) -> None:
            self._shell = shell

        def get_completions(
            self, document: Document, complete_event
        ):
            prefix = document.text_before_cursor
            if not prefix.strip():
                return

            for s in self._shell._last_suggestions[:8]:
                score_label = f" [{s.final_score:.2f}]" if self._shell._show_scores else ""
                display = f"{s.word}{score_label}"
                yield Completion(
                    text=s.word,
                    start_position=-len(prefix),
                    display=display,
                    display_meta=f"source={s.source}",
                )


# ── Shell ─────────────────────────────────────────────────────────────────────

class Shell:
    """
    Interactive autocomplete shell.

    Usage::

        shell = Shell(engine)
        asyncio.run(shell.run())
    """

    BANNER = """
╔══════════════════════════════════════════════════════╗
║         UNGA BUNGA AUTO-COMPLETE  v1.0.0             ║
║   Intelligent prefix completion • Type & explore     ║
║   Tab=accept  Ctrl+N/P=cycle  F1=stats  :help=help   ║
╚══════════════════════════════════════════════════════╝
"""

    def __init__(
        self,
        engine,  # SuggestionEngine
        theme: str = "dark",
        max_suggestions: int = 8,
        show_scores: bool = False,
        show_ghost: bool = True,
        history_file: Optional[Path] = None,
    ) -> None:
        self._engine = engine
        self._theme_name = theme
        self._theme = THEMES.get(theme, THEMES["dark"])
        self._max_suggestions = max_suggestions
        self._show_scores = show_scores
        self._show_ghost = show_ghost
        self._history_file = history_file or Path.home() / ".unga_bunga" / "cli_history"

        self._last_suggestions: List = []
        self._current_index: int = 0
        self._show_diagnostics = False
        self._query_count = 0
        self._context_tokens: List[str] = []

        if HAS_RICH:
            self._console = Console(highlight=False)
        else:
            self._console = None

        self._session: Optional[PromptSession] = None

    # ── Setup ──────────────────────────────────────────────────────────────

    def _build_key_bindings(self) -> KeyBindings:
        """Construct all custom key bindings."""
        kb = KeyBindings()

        @kb.add("tab")
        def accept_suggestion(event):
            """Accept top suggestion or cycle."""
            buf = event.app.current_buffer
            if self._last_suggestions:
                top = self._last_suggestions[self._current_index]
                prefix = buf.text
                # Replace buffer text with full suggestion
                buf.text = top.word
                buf.cursor_position = len(top.word)

        @kb.add("c-n")
        def next_suggestion(event):
            """Cycle to next suggestion."""
            if self._last_suggestions:
                self._current_index = (self._current_index + 1) % len(self._last_suggestions)
                buf = event.app.current_buffer
                s = self._last_suggestions[self._current_index]
                buf.text = s.word
                buf.cursor_position = len(s.word)

        @kb.add("c-p")
        def prev_suggestion(event):
            """Cycle to previous suggestion."""
            if self._last_suggestions:
                self._current_index = (self._current_index - 1) % len(self._last_suggestions)
                buf = event.app.current_buffer
                s = self._last_suggestions[self._current_index]
                buf.text = s.word
                buf.cursor_position = len(s.word)

        @kb.add("f1")
        def toggle_diagnostics(event):
            """Toggle diagnostics panel."""
            self._show_diagnostics = not self._show_diagnostics

        @kb.add("f2")
        def toggle_scores(event):
            """Toggle score breakdown display."""
            self._show_scores = not self._show_scores

        @kb.add("escape")
        def clear_suggestions(event):
            """Clear current suggestions."""
            self._last_suggestions.clear()
            self._current_index = 0

        return kb

    def _build_prompt_style(self) -> Style:
        t = self._theme
        return Style.from_dict({
            "prompt":           t["prompt"],
            "auto-suggestion":  f"italic {t['ghost']}",
            "completion-menu.completion":         t["suggestion_normal"],
            "completion-menu.completion.current": t["suggestion_selected"],
        })

    # ── Prompt ─────────────────────────────────────────────────────────────

    def _get_prompt_tokens(self) -> StyleAndTextTuples:
        t = self._theme
        stats = f" [{self._engine.trie.word_count}w]" if self._show_diagnostics else ""
        return [
            ("class:prompt", f"ub{stats} ❯ "),
        ]

    # ── Output helpers ─────────────────────────────────────────────────────

    def _print(self, text: str, style: str = "") -> None:
        if self._console:
            self._console.print(text, style=style, highlight=False)
        else:
            print(text)

    def _print_suggestions(self, prefix: str) -> None:
        """Print suggestion list below prompt (for non-ghost-text display)."""
        if not self._last_suggestions or not self._console:
            return

        t = self._theme
        table = Table(show_header=True, header_style=t["header"],
                      border_style=t["border"], box=None, padding=(0, 1))
        table.add_column("#", style="dim", width=3)
        table.add_column("Suggestion", min_width=20)
        table.add_column("Score", width=6)
        table.add_column("Source", width=8)

        for i, s in enumerate(self._last_suggestions[: self._max_suggestions], 1):
            score_color = (
                t["score_high"] if s.final_score >= 0.7
                else t["score_mid"] if s.final_score >= 0.4
                else t["score_low"]
            )
            marker = "▶ " if i - 1 == self._current_index else "  "
            table.add_row(
                marker + str(i),
                s.word,
                f"[{score_color}]{s.final_score:.2f}[/]",
                s.source,
            )

        self._console.print(table)

    def _print_diagnostics(self) -> None:
        """Print engine diagnostics panel."""
        if not self._console:
            return

        stats = self._engine.stats()
        t = self._theme

        table = Table(title="Engine Diagnostics", border_style=t["border"],
                      header_style=t["header"])
        table.add_column("Metric")
        table.add_column("Value")

        trie = stats.get("trie", {})
        cache = stats.get("cache", {})
        queries = stats.get("queries", {})

        table.add_row("Words in trie", str(trie.get("word_count", "?")))
        table.add_row("Trie nodes", str(trie.get("node_count", "?")))
        table.add_row("Est. memory", f"{trie.get('estimated_memory_mb', '?')} MB")
        table.add_row("Cache size", str(cache.get("size", "?")))
        table.add_row("Cache hit rate", f"{cache.get('hit_rate', 0):.1%}")
        table.add_row("Total queries", str(queries.get("total", "?")))
        table.add_row("Avg latency", f"{queries.get('avg_latency_ms', 0):.2f} ms")

        self._console.print(table)

    # ── Command handling ───────────────────────────────────────────────────

    async def _handle_command(self, text: str) -> bool:
        """
        Handle colon-prefixed commands.

        Returns:
            True if a command was handled, False if input should be treated
            as regular text.
        """
        cmd = text.strip()

        if cmd == ":help" or cmd == "/help":
            self._print_help()
            return True

        if cmd == ":stats":
            self._print_diagnostics()
            return True

        if cmd == ":reset":
            self._engine.ranking.reset_session()
            self._context_tokens.clear()
            self._print("[green]✓ Session reset[/green]")
            return True

        if cmd == ":quit" or cmd == ":q":
            raise EOFError

        if cmd.startswith(":train "):
            arg = cmd[7:].strip()
            path = Path(arg)
            if path.exists():
                from ..training import TrainingPipeline
                pipeline = TrainingPipeline()
                result = await pipeline.train_file(path, self._engine)
                self._print(
                    f"[green]✓ Trained {result.token_count} tokens from {path.name}[/green]\n"
                    f"  Vocab size: {result.vocab_size}, Time: {result.elapsed_s:.2f}s"
                )
            else:
                # Treat as inline text
                count = await self._engine.train_text(arg)
                self._print(f"[green]✓ Trained {count} tokens from inline text[/green]")
            return True

        if cmd.startswith(":train"):
            self._print("[yellow]Usage: :train <file_path_or_text>[/yellow]")
            return True

        if cmd == ":snapshot":
            trie_json = self._engine.trie.to_json()
            self._print(f"[green]✓ Snapshot ready ({self._engine.trie.word_count} words)[/green]")
            self._print("[dim](Attach a Storage instance to persist)[/dim]")
            return True

        if cmd == ":clear":
            if self._console:
                self._console.clear()
            return True

        if cmd == ":diagnostics":
            self._show_diagnostics = not self._show_diagnostics
            self._print(f"[blue]Diagnostics: {'ON' if self._show_diagnostics else 'OFF'}[/blue]")
            return True

        if cmd == ":scores":
            self._show_scores = not self._show_scores
            self._print(f"[blue]Score display: {'ON' if self._show_scores else 'OFF'}[/blue]")
            return True

        return False

    def _print_help(self) -> None:
        if self._console:
            self._console.print(Panel(
                "[bold]UNGA BUNGA AUTO-COMPLETE — Help[/bold]\n\n"
                "[cyan]Keyboard shortcuts:[/cyan]\n"
                "  Tab        Accept top suggestion / ghost text\n"
                "  Ctrl+N     Next suggestion\n"
                "  Ctrl+P     Previous suggestion\n"
                "  F1         Toggle diagnostics panel\n"
                "  F2         Toggle score breakdown\n"
                "  Escape     Clear suggestions\n"
                "  Ctrl+D     Quit\n\n"
                "[cyan]Commands:[/cyan]\n"
                "  :help          Show this help\n"
                "  :stats         Show engine statistics\n"
                "  :train <text>  Train on text or file path\n"
                "  :reset         Reset session learning\n"
                "  :snapshot      Create trie snapshot\n"
                "  :clear         Clear screen\n"
                "  :scores        Toggle score display\n"
                "  :quit          Exit shell\n\n"
                "[dim]Start typing to see autocomplete suggestions.[/dim]",
                title="Help",
                border_style="blue",
            ))
        else:
            print(self.BANNER)

    # ── Query loop ─────────────────────────────────────────────────────────

    async def _on_text_changed(self, prefix: str) -> None:
        """Called on every keystroke; updates suggestion cache."""
        if not prefix.strip():
            self._last_suggestions.clear()
            return

        try:
            result = await self._engine.query(
                prefix,
                context_tokens=self._context_tokens,
            )
            self._last_suggestions = result.suggestions
            self._current_index = 0
            self._query_count += 1
        except Exception as exc:
            logger.debug("Query failed: %s", exc)
            self._last_suggestions.clear()

    # ── Main run loop ──────────────────────────────────────────────────────

    async def run(self) -> None:
        """Start the interactive shell.  Runs until Ctrl+D or :quit."""
        await self._engine.start()

        if self._console:
            self._console.print(self.BANNER, style="bold blue")
            self._console.print(
                f"Engine ready: {self._engine.trie.word_count} words loaded.\n"
                "Type :help for commands.  Ctrl+D to quit.\n",
                style="dim",
            )
        else:
            print(self.BANNER)

        if not HAS_PROMPT_TOOLKIT:
            await self._run_basic()
            return

        from prompt_toolkit.history import FileHistory

        self._history_file.parent.mkdir(parents=True, exist_ok=True)
        history = FileHistory(str(self._history_file))

        self._session = PromptSession(
            message=self._get_prompt_tokens,
            history=history,
            auto_suggest=EngineAutoSuggest(self) if self._show_ghost else None,
            completer=EngineCompleter(self),
            complete_while_typing=True,
            key_bindings=self._build_key_bindings(),
            style=self._build_prompt_style(),
            enable_history_search=True,
            mouse_support=False,
        )

        while True:
            try:
                # Run query in background while waiting for input
                text = await self._session.prompt_async(
                    refresh_interval=0.2,
                )
            except KeyboardInterrupt:
                self._print("\n[yellow]Interrupted. :quit to exit.[/yellow]")
                continue
            except EOFError:
                self._print("\n[dim]Goodbye.[/dim]")
                break

            text = text.strip()
            if not text:
                continue

            if text.startswith(":") or text.startswith("/"):
                try:
                    await self._handle_command(text)
                except EOFError:
                    self._print("\n[dim]Goodbye.[/dim]")
                    break
                continue

            # Regular text — query and show results
            await self._on_text_changed(text)

            if self._last_suggestions:
                self._print_suggestions(text)
                if self._show_scores and self._last_suggestions:
                    self._print("\n[dim]Score breakdown:[/dim]")
                    for s in self._last_suggestions[:3]:
                        self._print(f"  [dim]{s.explain()}[/dim]")
            else:
                self._print(f"[dim]No suggestions for '{text}'[/dim]")

            # Record as context
            words = text.split()
            self._context_tokens.extend(words)
            self._context_tokens = self._context_tokens[-5:]  # keep last 5

        await self._engine.shutdown()

    async def _run_basic(self) -> None:
        """Fallback basic input loop without prompt_toolkit."""
        print("Basic mode (prompt_toolkit not available)")
        await self._engine.start()

        while True:
            try:
                text = input("ub ❯ ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nGoodbye.")
                break

            if not text:
                continue

            if text.startswith(":"):
                try:
                    await self._handle_command(text)
                except EOFError:
                    print("\nGoodbye.")
                    break
                continue

            result = await self._engine.query_immediate(text)
            if result.suggestions:
                for i, s in enumerate(result.suggestions[:5], 1):
                    marker = "→" if i == 1 else " "
                    print(f"  {marker} {s.word:30s} [{s.final_score:.2f}]")
            else:
                print(f"  (no suggestions for '{text}')")

        await self._engine.shutdown()


# ── Entry point ───────────────────────────────────────────────────────────────

def launch_shell(engine=None, theme: str = "dark", corpus: Optional[str] = None) -> None:
    """
    Convenience launcher for the CLI shell.

    Args:
        engine:  Pre-configured SuggestionEngine.  Creates a default one if None.
        theme:   Color theme name.
        corpus:  Optional text to pre-train on before launching.
    """
    from ..engine.suggestion_engine import SuggestionEngine

    if engine is None:
        engine = SuggestionEngine()

    shell = Shell(engine, theme=theme)

    async def _main():
        await engine.start()
        if corpus:
            await engine.train_text(corpus)
        await shell.run()

    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        pass
