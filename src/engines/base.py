"""Engine abstraction shared by all solving backends.

Every engine (Chrome/Selenium, Camoufox/Playwright) resolves a challenge and
returns a normalized ``SolveResult``. The controller maps that into the
FlareSolverr ``/v1`` response, so the API is identical regardless of which
engine solved the request.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

from dtos import V1RequestBase


@dataclass
class SolveResult:
    """Engine-agnostic result of resolving a single request.

    ``headers``, ``response`` and ``screenshot`` stay ``None`` when not
    requested; the controller only serializes the fields that were populated,
    matching FlareSolverr's original response shape.
    """
    url: str = ""
    status: int = 200
    cookies: list = field(default_factory=list)
    user_agent: str = ""
    message: str = ""
    headers: Optional[dict] = None
    response: Optional[str] = None
    screenshot: Optional[str] = None
    turnstile_token: Optional[str] = None


class Engine(ABC):
    """A pluggable challenge-solving backend."""

    name: str = "engine"

    @abstractmethod
    def solve(self, req: V1RequestBase, method: str, timeout: float) -> SolveResult:
        """Resolve the challenge for ``req`` and return a ``SolveResult``.

        Implementations own their own browser and session lifecycle. They raise
        on failure (blocked IP, timeout, unsolved challenge); the controller
        decides whether to fall back to another engine.
        """
        raise NotImplementedError
