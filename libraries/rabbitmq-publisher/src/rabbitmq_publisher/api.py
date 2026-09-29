"""Convenient public import surface for :mod:`rabbitmq_publisher`.

Applications should import the common library interface from this module::

    from rabbitmq_publisher import api

    async with api.Publisher(url) as publisher:
        await publisher.publish('events', 'created', b'...')

The other library modules also expose supported public symbols. Their names
follow the same convention as this module: names beginning with ``_`` are
implementation details, while non-underscored names are safe to rely on.
"""

from . import errors, publisher

ParameterError = errors.ParameterError
Publisher = publisher.Publisher
PublisherClosed = errors.PublisherClosed
PublisherError = errors.PublisherError
PublisherUnavailable = errors.PublisherUnavailable

__all__ = [
    'ParameterError',
    'Publisher',
    'PublisherClosed',
    'PublisherError',
    'PublisherUnavailable',
]
