"""Collection of Exception classes used by the FusionSolar package"""


class FusionSolarException(Exception):
    """Base class for all exceptions."""

    pass


class AuthenticationException(FusionSolarException):
    """Issues with the supplied username or password"""

    pass
