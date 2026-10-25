class ApplicationError(Exception):
    """Base class for failures of a use case (as opposed to a domain rule)."""


class RefillUnavailable(ApplicationError):
    """The requested stamina source is exhausted or not permitted."""


class NoTeamAvailable(ApplicationError):
    """No team was supplied and automatic team building is disabled."""


class ConfigError(ApplicationError):
    """The settings file is unreadable or has values of the wrong type."""


class InvalidRuns(ApplicationError):
    """A run count was negative."""


class CollectionError(ApplicationError):
    """A card collection file is unreadable or malformed."""
