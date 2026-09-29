import asyncio
import contextlib
import datetime
import logging
import random
import re
import typing as t

import pydantic
from pamqp import commands

from rabbitmq_publisher import connection, errors

_NAME_PATTERN = re.compile(r'^[\w:.-]+$')
_MAX_SHORT_STRING = 256


class PublisherKwargs(t.TypedDict, total=False):
    """Keyword arguments accepted by :meth:`Publisher.publish`."""

    mandatory: bool
    app_id: str | None
    content_encoding: str | None
    content_type: str | None
    correlation_id: str | None
    delivery_mode: int | None
    expiration: str | None
    headers: dict[str, t.Any] | None
    message_id: str | None
    message_type: str | None
    priority: int | None
    reply_to: str | None
    timestamp: datetime.datetime | None
    user_id: str | None


AMQPShortString = t.Annotated[
    str, pydantic.Field(max_length=_MAX_SHORT_STRING)
]


class _ValidatedParameters(pydantic.BaseModel):
    exchange: t.Annotated[
        AMQPShortString, pydantic.Field(pattern=_NAME_PATTERN)
    ]
    routing_key: AMQPShortString
    message_body: bytes | str
    mandatory: bool = False
    app_id: AMQPShortString | None = None
    content_encoding: AMQPShortString | None = None
    content_type: AMQPShortString | None = None
    correlation_id: AMQPShortString | None = None
    delivery_mode: t.Annotated[int, pydantic.Field(ge=1, le=2)] | None = None
    expiration: AMQPShortString | None = None
    headers: dict[str, t.Any] | None = None
    message_id: AMQPShortString | None = None
    message_type: AMQPShortString | None = None
    priority: t.Annotated[int, pydantic.Field(ge=0, le=255)] | None = None
    reply_to: AMQPShortString | None = None
    timestamp: datetime.datetime | None = None
    user_id: AMQPShortString | None = None


class Publisher:
    """Maintain one AMQP connection and publish messages through it.

    The publisher is intended to be owned by an application lifespan. Entering
    the async context starts connection supervision and returns immediately;
    it does not require RabbitMQ to be available. Connection failures are
    logged and retried in the background. The publisher does not impose a
    publication deadline; callers can apply one with ``asyncio.timeout``.

    The publisher uses one connection and one channel, does not declare
    topology, does not buffer or replay messages, and does not request
    publisher confirmations. A successful :meth:`publish` means that the
    publication was written to the AMQP socket.

    Args:
        url: AMQP connection URL, using the ``amqp`` or ``amqps`` scheme.
    Raises:
        ValueError: If ``url`` is invalid.
    """

    _RETRY_BASE = 0.5
    _RETRY_MAX = 30.0

    def __init__(self, url: str) -> None:
        """Create a publisher without connecting to RabbitMQ.

        Connection attempts begin when the publisher is entered as an async
        context manager. Creating the publisher validates the URL but does not
        perform network I/O.

        Args:
            url: AMQP connection URL, using the ``amqp`` or ``amqps`` scheme.

        Raises:
            ValueError: If ``url`` is invalid.
        """
        connection.parse_url(url)
        self._logger = logging.getLogger(__name__)
        self._url = url
        self._stopping = asyncio.Event()
        self._ready = asyncio.Event()
        self._publish_lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None
        self._connection: connection.Connection | None = None
        self._last_error: BaseException | None = None
        self._started = False
        self._closed = False

    async def __aenter__(self) -> t.Self:
        """Start background connection supervision and return this publisher.

        Returns:
            This publisher.

        Raises:
            PublisherClosed: If this publisher has already been started or
                closed.
        """
        if self._started or self._closed:
            raise errors.PublisherClosed('Publisher cannot be started again')
        self._started = True
        self._task = asyncio.create_task(self._supervise())
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object | None,
        /,
    ) -> None:
        """Stop connection supervision and close the AMQP connection."""
        self._closed = True
        self._stopping.set()
        self._ready.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def publish(
        self,
        exchange: str = 'amq.direct',
        routing_key: str = '',
        message_body: bytes | str = b'',
        **kwargs: t.Unpack[PublisherKwargs],
    ) -> None:
        """Publish one message to an exchange and routing key.

        The method writes the AMQP publication to the socket and returns
        without waiting for a broker confirmation. RabbitMQ topology must be
        created externally. If ``mandatory`` is true and RabbitMQ returns the
        publication, the return is consumed and logged; it is not exposed to
        the caller in this version.

        Args:
            exchange: Exchange receiving the publication. The default is
                ``amq.direct``.
            routing_key: Routing key for the publication.
            message_body: Message payload as UTF-8 text or bytes.
            mandatory: Whether RabbitMQ should return an unroutable
                publication.
            app_id: Application identifier message property.
            content_encoding: Content encoding message property.
            content_type: Content type message property.
            correlation_id: Correlation identifier message property.
            delivery_mode: Delivery mode message property. Valid values are
                ``1`` and ``2``.
            expiration: Expiration message property as a string.
            headers: Application headers message property.
            message_id: Message identifier message property.
            message_type: Message type message property.
            priority: Message priority from ``0`` through ``255``.
            reply_to: Reply-to destination message property.
            timestamp: Timestamp message property.
            user_id: User identifier message property.

        Raises:
            PublisherClosed: If the publisher is not running or has been
                closed.
            PublisherUnavailable: If RabbitMQ is unavailable, has blocked the
                connection, has paused the channel, or the publication cannot
                be written.
            ParameterError: If a publication argument fails Pydantic
                validation. The underlying ``pydantic.ValidationError`` is
                available as ``error.validation_error``.

        Returns:
            ``None`` after the publication has been written to the socket.
        """
        try:
            params = _ValidatedParameters.model_validate(
                {
                    'exchange': exchange,
                    'routing_key': routing_key,
                    'message_body': message_body,
                }
                | kwargs,
            )
        except pydantic.ValidationError as e:
            raise errors.ParameterError(e) from None

        if not self._started or self._closed:
            raise errors.PublisherClosed('Publisher is not running')

        body = (
            message_body.encode('utf-8')
            if isinstance(message_body, str)
            else message_body
        )
        properties = commands.Basic.Properties(
            app_id=params.app_id,
            content_encoding=params.content_encoding,
            content_type=params.content_type,
            correlation_id=params.correlation_id,
            delivery_mode=params.delivery_mode,
            expiration=params.expiration,
            headers=params.headers,
            message_id=params.message_id,
            message_type=params.message_type,
            priority=params.priority,
            reply_to=params.reply_to,
            timestamp=params.timestamp,
            user_id=params.user_id,
        )
        try:
            await self._ready.wait()
            if self._closed:
                self._raise_closed()
            async with self._publish_lock:
                current = self._connection
                if current is None or current.error is not None:
                    self._raise_unavailable()
                self._check_connection_status(current)
                await current.publish(
                    connection.frame_values(
                        exchange,
                        routing_key,
                        body,
                        params.mandatory,
                        properties,
                        current.frame_max,
                    )
                )
        except errors.PublisherError:
            raise
        except Exception as exc:  # noqa: BLE001
            self._last_error = exc
            current = self._connection
            if current is not None:  # pragma: no branch: paranoia
                await current.close()
            raise errors.PublisherUnavailable(
                'Writing to RabbitMQ failed'
            ) from exc

    async def _supervise(self) -> None:
        delay = self._RETRY_BASE
        while (
            not self._stopping.is_set()
        ):  # pragma: no branch: concurrent tests are hard
            current: connection.Connection | None = None
            try:
                current = await connection.connect(self._url)
                self._connection = current
                self._ready.set()
                delay = self._RETRY_BASE
                await current.wait_closed()
                if current.error is not None:
                    self._last_error = current.error
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self._last_error = exc
                self._logger.warning('RabbitMQ connection failed: %s', exc)
            finally:
                self._ready.clear()
                if (
                    self._connection is current
                ):  # pragma: no branch: race condition
                    self._connection = None
                if current is not None:
                    await current.close()

            if (
                not self._stopping.is_set()
            ):  # pragma: no branch: concurrent tests are hard
                try:
                    jittered_delay = random.uniform(  # noqa: S311
                        delay / 2, delay * 1.5
                    )
                    await asyncio.wait_for(
                        self._stopping.wait(), timeout=jittered_delay
                    )
                except TimeoutError:
                    delay = min(delay * 2, self._RETRY_MAX)

    def _raise_unavailable(self) -> t.NoReturn:
        error = errors.PublisherUnavailable('RabbitMQ is unavailable')
        if self._last_error is not None:
            raise error from self._last_error
        raise error

    @staticmethod
    def _check_connection_status(current: connection.Connection) -> None:
        if current.blocked_reason is not None:
            Publisher._raise_blocked(current.blocked_reason)
        if not current.flow_active:
            Publisher._raise_flow_controlled()

    @staticmethod
    def _raise_blocked(reason: str) -> t.NoReturn:
        raise errors.PublisherUnavailable(
            f'RabbitMQ connection is blocked: {reason}'
        )

    @staticmethod
    def _raise_flow_controlled() -> t.NoReturn:
        raise errors.PublisherUnavailable('RabbitMQ flow control is active')

    @staticmethod
    def _raise_closed() -> t.NoReturn:
        raise errors.PublisherClosed('Publisher is closed')
