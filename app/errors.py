class BridgeError(Exception):
    """Base application exception."""


class ValidationError(BridgeError):
    """Raised when a model result is unsafe or unsupported."""


class UpstreamServiceError(BridgeError):
    """Raised when an upstream dependency fails."""
