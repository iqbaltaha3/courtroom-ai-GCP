"""Ordered thread results without queuing an entire document at once."""

from collections import deque
from contextvars import copy_context


def bounded_results(pool, function, items, window):
    """Keep a limited number of tasks queued while propagating per-case context."""
    items = iter(items)
    pending = deque()

    def submit_next():
        """Schedule one item in a copied context without consuming the whole input."""
        try:
            item = next(items)
        except StopIteration:
            return False
        pending.append(pool.submit(copy_context().run, function, item))
        return True

    try:
        for _ in range(window):
            if not submit_next():
                break
        while pending:
            yield pending.popleft().result()
            submit_next()
    finally:
        for future in pending:
            future.cancel()
