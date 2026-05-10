"""
UNGA BUNGA AUTO-COMPLETE — Event System
========================================
A typed, async-first event bus with priority support, dead-letter handling,
and thread-safe subscription management.

Architecture:
    Events are dataclasses.  The bus maintains a subscriber registry keyed
    by event type (exact match + MRO walk for base-class subscriptions).
    Async subscribers are awaited; sync subscribers run in an executor so
    the event loop is never blocked.

Thread safety:
    Subscription mutations use a threading.Lock.
    Dispatching is lock-free after the subscriber snapshot is taken.

Performance:
    Subscriber list is copied on dispatch to allow concurrent mutation
    without holding the lock during handler execution.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Coroutine, Dict, List, Optional, Type, Union

logger = logging.getLogger(__name__)


# ── Event priority ────────────────────────────────────────────────────────────

class Priority(Enum):
    LOW = auto()
    NORMAL = auto()
    HIGH = auto()
    CRITICAL = auto()


# ── Base event ────────────────────────────────────────────────────────────────

@dataclass
class BaseEvent:
    """All events must inherit from this class."""
    priority: Priority = field(default=Priority.NORMAL, compare=False)
    cancelled: bool = field(default=False, compare=False, repr=False)

    def cancel(self) -> None:
        """Mark event as cancelled; handlers can inspect this."""
        self.cancelled = True


# ── Concrete events ───────────────────────────────────────────────────────────

@dataclass
class QueryEvent(BaseEvent):
    """Fired when a new autocomplete query arrives."""
    prefix: str = ""
    context_tokens: list = field(default_factory=list)
    session_id: str = ""


@dataclass
class SuggestionReadyEvent(BaseEvent):
    """Fired when suggestions are ready for a query."""
    query_id: str = ""
    suggestions: list = field(default_factory=list)
    elapsed_ms: float = 0.0


@dataclass
class IndexUpdateEvent(BaseEvent):
    """Fired after the trie index is updated."""
    words_added: int = 0
    words_removed: int = 0


@dataclass
class TrainingCompleteEvent(BaseEvent):
    """Fired when a corpus training pass finishes."""
    corpus_name: str = ""
    token_count: int = 0
    vocab_size: int = 0
    elapsed_s: float = 0.0


@dataclass
class PersistenceEvent(BaseEvent):
    """Fired on persistence lifecycle events."""
    class Kind(Enum):
        SAVE_START = auto()
        SAVE_COMPLETE = auto()
        SAVE_FAILED = auto()
        LOAD_START = auto()
        LOAD_COMPLETE = auto()
        LOAD_FAILED = auto()
        SNAPSHOT_CREATED = auto()
        RECOVERY_TRIGGERED = auto()

    kind: Kind = Kind.SAVE_START
    path: str = ""
    error: Optional[str] = None


@dataclass
class LifecycleEvent(BaseEvent):
    """Application lifecycle transitions."""
    class Phase(Enum):
        STARTUP = auto()
        WARMUP = auto()
        RUNNING = auto()
        SHUTDOWN = auto()
        CRASHED = auto()

    phase: Phase = Phase.STARTUP
    detail: str = ""


@dataclass
class ErrorEvent(BaseEvent):
    """Global error bus — any subsystem can push errors here."""
    source: str = ""
    message: str = ""
    exc: Optional[Exception] = field(default=None, repr=False)
    recoverable: bool = True


# ── Handler types ────────────────────────────────────────────────────────────

SyncHandler = Callable[[BaseEvent], None]
AsyncHandler = Callable[[BaseEvent], Coroutine[Any, Any, None]]
AnyHandler = Union[SyncHandler, AsyncHandler]


# ── Event bus ────────────────────────────────────────────────────────────────

class EventBus:
    """
    Central event bus for the UNGA BUNGA platform.

    Usage::

        bus = EventBus.get_instance()

        # Subscribe
        bus.subscribe(QueryEvent, my_async_handler)

        # Dispatch (from sync context)
        bus.dispatch_sync(QueryEvent(prefix="hel"))

        # Dispatch (from async context)
        await bus.dispatch(QueryEvent(prefix="hel"))

    Subscription scoping:
        Subscribing to *BaseEvent* receives ALL events.
        Subscribing to *QueryEvent* receives only QueryEvent instances.
    """

    _instance: Optional[EventBus] = None
    _lock: threading.Lock = threading.Lock()

    def __new__(cls) -> EventBus:
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._ready = False
        return cls._instance

    def __init__(self) -> None:
        if self._ready:
            return
        self._sub_lock = threading.Lock()
        # event_type → list of (priority, handler)
        self._subscribers: Dict[Type[BaseEvent], List[tuple]] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._executor = None
        self._dead_letters: List[BaseEvent] = []
        self._ready = True
        logger.debug("EventBus initialised")

    # ── Loop binding ──────────────────────────────────────────────────────

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Bind to an event loop for async dispatch.  Call once on startup."""
        self._loop = loop
        logger.debug("EventBus bound to event loop")

    # ── Subscription ──────────────────────────────────────────────────────

    def subscribe(
        self,
        event_type: Type[BaseEvent],
        handler: AnyHandler,
        priority: Priority = Priority.NORMAL,
    ) -> None:
        """
        Register *handler* for events of *event_type*.

        Args:
            event_type: The event class to listen for.
            handler:    Sync or async callable receiving the event.
            priority:   Higher priority handlers run first.

        Thread safety: Protected by _sub_lock.
        """
        with self._sub_lock:
            bucket = self._subscribers.setdefault(event_type, [])
            bucket.append((priority, handler))
            # Keep sorted descending (CRITICAL first)
            bucket.sort(key=lambda x: x[0].value, reverse=True)
        logger.debug("Subscribed %s to %s", handler, event_type.__name__)

    def unsubscribe(self, event_type: Type[BaseEvent], handler: AnyHandler) -> None:
        """Remove *handler* from *event_type* subscriptions."""
        with self._sub_lock:
            bucket = self._subscribers.get(event_type, [])
            self._subscribers[event_type] = [
                (p, h) for p, h in bucket if h is not handler
            ]

    # ── Dispatch ──────────────────────────────────────────────────────────

    def _collect_handlers(self, event: BaseEvent) -> List[AnyHandler]:
        """
        Walk the MRO of the event type to collect all applicable handlers.
        Handlers are de-duplicated and ordered by priority.
        """
        seen: set = set()
        result: List[tuple] = []
        with self._sub_lock:
            for cls in type(event).__mro__:
                if cls not in self._subscribers:
                    continue
                for priority, handler in self._subscribers[cls]:
                    hid = id(handler)
                    if hid not in seen:
                        seen.add(hid)
                        result.append((priority, handler))
        result.sort(key=lambda x: x[0].value, reverse=True)
        return [h for _, h in result]

    async def dispatch(self, event: BaseEvent) -> None:
        """
        Dispatch *event* to all subscribers.  Awaits async handlers.

        Handlers run in priority order.  If a handler raises, the error
        is logged but dispatch continues (fail-open for resilience).

        Args:
            event: Event instance to dispatch.
        """
        handlers = self._collect_handlers(event)
        if not handlers:
            self._dead_letters.append(event)
            if len(self._dead_letters) > 256:
                self._dead_letters = self._dead_letters[-256:]
            return

        for handler in handlers:
            if event.cancelled:
                break
            try:
                if asyncio.iscoroutinefunction(handler):
                    await handler(event)
                else:
                    loop = asyncio.get_event_loop()
                    await loop.run_in_executor(None, handler, event)
            except Exception as exc:  # noqa: BLE001
                logger.error("Handler %s raised for %s: %s", handler, type(event).__name__, exc)

    def dispatch_sync(self, event: BaseEvent) -> None:
        """
        Thread-safe synchronous dispatch.  Schedules on the bound event loop
        if available, otherwise calls sync handlers directly.

        Args:
            event: Event instance to dispatch.
        """
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self.dispatch(event), self._loop)
        else:
            # Fallback: run sync handlers only
            handlers = self._collect_handlers(event)
            for handler in handlers:
                if event.cancelled:
                    break
                if not asyncio.iscoroutinefunction(handler):
                    try:
                        handler(event)
                    except Exception as exc:  # noqa: BLE001
                        logger.error("Sync handler %s raised: %s", handler, exc)

    # ── Dead letters ──────────────────────────────────────────────────────

    @property
    def dead_letters(self) -> List[BaseEvent]:
        """Events that had no subscribers (diagnostic aid)."""
        return list(self._dead_letters)

    # ── Singleton accessor ────────────────────────────────────────────────

    @classmethod
    def get_instance(cls) -> EventBus:
        return cls()


def get_event_bus() -> EventBus:
    """Module-level shortcut."""
    return EventBus.get_instance()
