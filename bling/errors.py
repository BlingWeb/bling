"""bling's exception hierarchy. Catch BlingError for everything, or a specific subclass to
branch. Messages are actionable on purpose (they tell you how to fix the problem)."""

from __future__ import annotations


class BlingError(RuntimeError):
    """Base class for every error bling raises."""


class NotLoggedIn(BlingError):
    """The persistent profile has no valid cookie. Fix: run ``bling login``."""


class SessionBlocked(BlingError):
    """Browserling refused the session (plan/time/VM limit, firewall, duplicate, ...)."""


class NotReady(BlingError):
    """The remote canvas didn't come up before the timeout."""


class Timeout(BlingError):
    """A VM operation (e.g. a shell command) didn't finish in time."""


class EgressError(BlingError):
    """A file transfer (upload/download) or its curl token failed."""
