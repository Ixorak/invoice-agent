"""`watch`: check the inbox every N seconds and process each new invoice.

A file is processed only once its size has stopped changing, so a PDF that is
still being copied into the inbox is not read half-written.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

from .pipeline import SKIPPED, Agent, BatchResult, list_pdfs

MAX_BACKOFF_SECONDS = 600


def _size(path: Path) -> int | None:
    try:
        return path.stat().st_size
    except OSError:
        return None


def stable_files(paths: list[Path], wait_seconds: float = 1.0, sleep: Callable[[float], None] = time.sleep) -> list[Path]:
    """Keep the files whose size is the same before and after a short pause."""
    if not paths:
        return []
    before = {p: _size(p) for p in paths}
    sleep(wait_seconds)
    return [p for p in paths if before[p] and _size(p) == before[p]]


def watch(agent: Agent, interval: float, on_batch: Callable[[BatchResult], None],
          sleep: Callable[[float], None] = time.sleep, max_loops: int | None = None) -> None:
    """Loop until Ctrl+C (KeyboardInterrupt is left to the caller).

    After a failed run (e.g. Claude Code not reachable) the next attempt waits
    longer each time, up to 10 minutes, instead of calling it again every few seconds.
    """
    reported_skips: set[tuple[str, int | None]] = set()
    failures = 0
    loops = 0
    while max_loops is None or loops < max_loops:
        loops += 1
        candidates = stable_files(list_pdfs(agent.workspace.inbox), sleep=sleep)
        # A file skipped earlier (already processed) is not reported again on every pass.
        fresh = [p for p in candidates if (p.name, _size(p)) not in reported_skips]
        if fresh:
            batch = agent.process_inbox(fresh)
            for r in batch.results:
                if r.status == SKIPPED:
                    reported_skips.add((r.file, _size(agent.workspace.inbox / r.file)))
            on_batch(batch)
            failures = failures + 1 if batch.error else 0
        delay = interval if failures == 0 else min(interval * 2 ** failures, MAX_BACKOFF_SECONDS)
        if max_loops is None or loops < max_loops:
            sleep(delay)
