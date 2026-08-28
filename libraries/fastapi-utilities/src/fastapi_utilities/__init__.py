import logging
from importlib import metadata

version = metadata.version('fastapi-utilities')


def get_logger(obj: object) -> logging.Logger:
    """Retrieve a named logger for an object."""
    logger = logging.getLogger(obj.__module__)
    if name := getattr(obj, '__name__', None):
        logger = logger.getChild(name)
    else:
        logger = logger.getChild(obj.__class__.__name__)
    return logger


def unwrap[T](value: T | None) -> T:
    """Unwrap a value, raising an error if it is None."""
    if value is None:
        raise ValueError('Value unexpectedly None.')
    return value


def unwrap_as[T](value: object | None, cls: type[T]) -> T:
    """Unwrap a value as `cls`, raising if it cannot be unwrapped.

    Args:
        value: The value to unwrap.
        cls: The expected type.

    Returns:
        The unwrapped value.

    Raises:
        ValueError: If the value is None.
        TypeError: If the value is not of the expected type.
    """
    if isinstance(value, cls):
        return value
    if value is None:
        raise ValueError(f'Expected {cls.__name__}, got None.')
    raise TypeError(f'Expected {cls.__name__}, got {type(value).__name__}.')


del metadata
