"""Gateway error types, in a module with no gateway imports."""
from dokkan.application.errors import ApplicationError


class GatewayError(ApplicationError):
    """The game API could not be reached or returned an unusable reply."""