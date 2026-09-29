import pydantic


class PublisherError(Exception):
    """Base class for errors raised by :class:`Publisher`."""


class PublisherUnavailable(PublisherError):  # noqa: N818
    """The publisher cannot currently write to RabbitMQ.

    This exception covers connection failures, broker resource blocks, channel
    flow control, and failures while writing to the socket.
    The original exception may be available through ``__cause__``.
    """


class PublisherClosed(PublisherError):  # noqa: N818
    """The publisher has not been started or has been shut down."""


class ParameterError(PublisherError):
    """Publication parameters failed Pydantic validation.

    Args:
        validation_error: The Pydantic validation error containing the field
            errors.

    Attributes:
        validation_error: The underlying Pydantic validation error.
    """

    def __init__(self, validation_error: pydantic.ValidationError) -> None:
        super().__init__(str(validation_error))
        self.validation_error = validation_error
