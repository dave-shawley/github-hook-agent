import functools
import logging
import typing as t

import fastapi
import pydantic
import pydantic_settings


def get_logger(obj: object) -> logging.Logger:
    """Retrieve an intelligently named logger for `obj`"""
    logger = logging.getLogger(__package__)
    if isinstance(obj, type) or callable(obj):
        return logger.getChild(obj.__name__)
    return logger.getChild(obj.__class__.__name__)


def from_environment[Config: pydantic_settings.BaseSettings](
    cls: type[Config],
) -> Config:
    """Retrieve settings from the environment"""
    logger = get_logger(from_environment)
    logger.info('retrieving %s from environment', cls.__name__)
    try:
        return cls()
    except pydantic.ValidationError as error:
        missing_names: set[str] = set()
        for e in error.errors():
            if e['type'] == 'missing':
                missing_names.add(unwrap_as(e['loc'][0], str))
        if missing_names:
            logger.error(  # noqa: TRY400 - no need for traceback
                'Required environment variables not configured: %s',
                ', '.join(missing_names),
            )
        raise fastapi.HTTPException(500) from error


if not t.TYPE_CHECKING:
    from_environment = functools.cache(from_environment)


def unwrap_as[T](value: object | None, cls: type[T]) -> T:
    """Ensure that `value` is not None and of type `cls`"""
    if isinstance(value, cls):
        return value
    if value is None:
        raise ValueError('Value unexpectedly None')
    raise TypeError(
        f'Expected value of type {cls.__name__},'
        f' received {value.__class__.__name__}'
    )
