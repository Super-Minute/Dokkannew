class DomainError(Exception):
    """Base class for rule violations raised by the domain ring."""


class InvalidValue(DomainError):
    """A model was constructed with values that break its invariants."""


class InsufficientStamina(DomainError):
    """Stamina would drop below zero."""


class InvalidTeam(DomainError):
    """A team is empty, oversized, duplicated, or impossible to build."""
