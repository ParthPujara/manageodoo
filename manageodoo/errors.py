"""Typed exceptions so the CLI can print clean messages instead of tracebacks."""


class ManageodooError(Exception):
    """Base class for all expected, user-facing errors."""


class DetectionError(ManageodooError):
    """Raised when a path cannot be understood as an Odoo install."""


class EnvNotFound(ManageodooError):
    """Raised when a named environment is not in the registry."""


class EnvExists(ManageodooError):
    """Raised when registering a name that is already taken."""


class RunError(ManageodooError):
    """Raised when odoo-bin cannot be launched (missing python/odoo-bin)."""
