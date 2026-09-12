"""How a request's time is divided, for both engines.

The controller decides how much of `maxTimeout` each engine gets, and splits it
evenly so a fallback is still reachable (`_resolve_challenge`). This module holds
the part below that: what an engine may spend getting a browser, when it has to
stop solving, and how much of its share is left after each.

Both engines had the same formula written out separately, with the constant
named on one side and bare on the other. It is one rule, so it lives here.
"""
import time

# What an engine keeps back from its share to build the response once it stops
# solving. Reading the page, the cookies and possibly a screenshot are all round
# trips to a browser, and an engine that spends its last second still pressing a
# checkbox gets killed mid-answer and reports a timeout instead of the page it
# already had.
SOLVE_MARGIN_SECONDS = 3


def solve_deadline(started: float, timeout: float) -> float:
    """The monotonic time an engine must stop solving by.

    `started` and `timeout` are in the caller's own monotonic clock, which is
    `time.monotonic()` on the request thread and the event loop's clock on the
    stealth engine; only the arithmetic is shared.

    Never returns less than a second of solving, even on a budget smaller than
    the margin: a request that cannot finish is still worth one attempt, and the
    outer cap stops it either way.
    """
    return started + max(1.0, timeout - SOLVE_MARGIN_SECONDS)


def remaining_share(started: float, timeout: float) -> float:
    """What is left of an engine's share, counted from when it was handed over.

    Launching a browser takes seconds, and it used to happen before either
    engine started its own clock, so an engine spent its whole share solving on
    top of whatever the launch cost. maxTimeout covers the whole request, and
    the fallback engine only gets what the first one leaves, so the launch has
    to come out of the share rather than sit beside it.

    Never returns less than a second: a request that cannot finish is still
    worth the attempt, and the controller's own deadline stops it either way.
    """
    return max(1.0, timeout - (time.monotonic() - started))
