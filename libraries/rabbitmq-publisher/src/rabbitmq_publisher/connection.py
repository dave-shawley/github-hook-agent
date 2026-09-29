import asyncio
import contextlib
import dataclasses
import logging
import urllib.parse

from pamqp import body as body_frames
from pamqp import commands, frame, header, heartbeat

_DEFAULT_FRAME_MAX = 131_072


@dataclasses.dataclass(frozen=True)
class _ConnectionSettings:
    host: str
    port: int
    username: str
    password: str
    virtual_host: str
    ssl_enabled: bool
    heartbeat: int
    connection_timeout: float


def parse_url(url: str) -> _ConnectionSettings:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in {'amqp', 'amqps'}:
        raise ValueError('AMQP URL must use amqp:// or amqps://')
    if parsed.hostname is None:
        raise ValueError('AMQP URL must include a host')
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError('AMQP URL has an invalid port') from exc
    query = urllib.parse.parse_qs(parsed.query)

    def query_int(name: str, default: int) -> int:
        try:
            return int(query.get(name, [str(default)])[0])
        except ValueError as exc:
            raise ValueError(f'AMQP URL has an invalid {name}') from exc

    try:
        connection_timeout = float(query.get('connection_timeout', ['3.0'])[0])
    except ValueError as exc:
        raise ValueError('AMQP URL has an invalid connection_timeout') from exc
    if connection_timeout <= 0:
        raise ValueError('connection_timeout must be greater than zero')

    virtual_host = urllib.parse.unquote(parsed.path[1:] or '/')
    return _ConnectionSettings(
        host=parsed.hostname,
        port=port or (5671 if parsed.scheme == 'amqps' else 5672),
        username=urllib.parse.unquote(parsed.username or ''),
        password=urllib.parse.unquote(parsed.password or ''),
        virtual_host=virtual_host,
        ssl_enabled=parsed.scheme == 'amqps',
        heartbeat=query_int('heartbeat', 0),
        connection_timeout=connection_timeout,
    )


class Connection:
    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        frame_max: int,
    ) -> None:
        self._logger = logging.getLogger(__name__)
        self._reader = reader
        self._writer = writer
        self._frame_max = frame_max or _DEFAULT_FRAME_MAX
        self._closed = asyncio.Event()
        self._reader_task: asyncio.Task[None] | None = None
        self._heartbeat_interval = 0
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._blocked_reason: str | None = None
        self._flow_active = True
        self.error: BaseException | None = None

    @classmethod
    async def connect(cls, url: str) -> Connection:
        settings = parse_url(url)
        reader: asyncio.StreamReader | None = None
        writer: asyncio.StreamWriter | None = None
        try:
            async with asyncio.timeout(settings.connection_timeout):
                if settings.ssl_enabled:
                    reader, writer = await asyncio.open_connection(
                        settings.host,
                        settings.port,
                        ssl=True,
                        server_hostname=settings.host,
                    )
                else:
                    reader, writer = await asyncio.open_connection(
                        settings.host, settings.port
                    )
                connection = cls(reader, writer, 0)
                await connection._handshake(settings)
                connection._reader_task = asyncio.create_task(
                    connection._read_loop()
                )
                if connection._heartbeat_interval:
                    connection._heartbeat_task = asyncio.create_task(
                        connection._send_heartbeats()
                    )
                return connection
        except BaseException:  # noqa: BLE001
            if writer is not None:
                writer.close()
                with contextlib.suppress(Exception):
                    await writer.wait_closed()
            raise

    async def _handshake(self, settings: _ConnectionSettings) -> None:
        self._writer.write(frame.marshal(header.ProtocolHeader(), 0))
        await self._writer.drain()

        await self._expect(0, commands.Connection.Start)
        self._writer.write(
            frame.marshal(
                commands.Connection.StartOk(
                    client_properties={'product': 'rabbitmq-publisher'},
                    response=f'\0{settings.username}\0{settings.password}',
                    locale='en_US',
                ),
                0,
            )
        )
        await self._writer.drain()

        tune = await self._expect(0, commands.Connection.Tune)
        self._frame_max = tune.frame_max or _DEFAULT_FRAME_MAX
        heartbeat_interval = settings.heartbeat or tune.heartbeat
        self._heartbeat_interval = heartbeat_interval
        self._writer.write(
            frame.marshal(
                commands.Connection.TuneOk(
                    tune.channel_max, self._frame_max, heartbeat_interval
                ),
                0,
            )
        )
        self._writer.write(
            frame.marshal(commands.Connection.Open(settings.virtual_host), 0)
        )
        await self._writer.drain()
        await self._expect(0, commands.Connection.OpenOk)

        self._writer.write(frame.marshal(commands.Channel.Open(), 1))
        await self._writer.drain()
        await self._expect(1, commands.Channel.OpenOk)

    async def _expect[T](self, channel: int, expected: type[T]) -> T:
        actual_channel, value = await self._read_frame()
        if actual_channel == 0 and isinstance(
            value, commands.Connection.Close
        ):
            raise ConnectionError(
                f'RabbitMQ closed the connection: '
                f'{value.reply_code} {value.reply_text}'
            )
        if actual_channel != channel or not isinstance(value, expected):
            raise ConnectionError(
                f'Unexpected AMQP frame: channel={actual_channel}, '
                f'value={value!r}'
            )
        return value

    async def _read_frame(self) -> tuple[int, object]:
        prefix = await self._reader.readexactly(7)
        size = int.from_bytes(prefix[3:7], 'big')
        suffix = await self._reader.readexactly(size + 1)
        _, channel, value = frame.unmarshal(prefix + suffix)
        return channel, value

    async def _read_loop(self) -> None:
        try:
            while True:
                channel, value = await self._read_frame()
                if channel == 0:
                    await self._handle_connection_frame(value)
                elif channel == 1:
                    await self._handle_channel_frame(value)
                else:
                    raise ConnectionError(  # noqa: TRY301
                        f'Unexpected AMQP frame: channel={channel}, '
                        f'value={value!r}'
                    )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            self.error = exc
        finally:
            self._closed.set()

    async def _handle_connection_frame(self, value: object) -> None:
        if isinstance(value, heartbeat.Heartbeat):
            self._writer.write(frame.marshal(value, 0))
            await self._writer.drain()
        elif isinstance(value, commands.Connection.Close):
            self._writer.write(frame.marshal(commands.Connection.CloseOk(), 0))
            await self._writer.drain()
            raise ConnectionError(  # noqa: TRY301
                f'RabbitMQ closed the connection: '
                f'{value.reply_code} {value.reply_text}'
            )
        elif isinstance(value, commands.Connection.Blocked):
            self._blocked_reason = value.reason
            self._logger.warning(
                'RabbitMQ blocked the publishing connection: %s',
                value.reason,
            )
        elif isinstance(value, commands.Connection.Unblocked):
            self._blocked_reason = None
            self._logger.info('RabbitMQ unblocked the publishing connection')
        else:
            raise ConnectionError(  # noqa: TRY301
                f'Unexpected AMQP connection frame: {value!r}'
            )

    async def _handle_channel_frame(self, value: object) -> None:
        if isinstance(value, commands.Channel.Close):
            self._writer.write(frame.marshal(commands.Channel.CloseOk(), 1))
            await self._writer.drain()
            raise ConnectionError(  # noqa: TRY301
                f'RabbitMQ closed the channel: '
                f'{value.reply_code} {value.reply_text}'
            )
        if isinstance(value, commands.Channel.Flow):
            self._flow_active = value.active is not False
            self._writer.write(
                frame.marshal(commands.Channel.FlowOk(value.active), 1)
            )
            await self._writer.drain()
            if not self._flow_active:
                self._logger.warning(
                    'RabbitMQ paused publishing on the channel'
                )
            else:
                self._logger.info('RabbitMQ resumed publishing on the channel')
            return
        if isinstance(value, commands.Basic.Return):
            await self._consume_returned_message(1, value)
            return
        raise ConnectionError(  # noqa: TRY301
            f'Unexpected AMQP channel frame: {value!r}'
        )

    async def _consume_returned_message(
        self, channel: int, returned: commands.Basic.Return
    ) -> None:
        header_channel, content_header = await self._read_frame()
        if header_channel != channel or not isinstance(
            content_header, header.ContentHeader
        ):
            raise ConnectionError(  # noqa: TRY301
                f'Unexpected returned message header: '
                f'channel={header_channel}, value={content_header!r}'
            )

        remaining = content_header.body_size
        while remaining:
            body_channel, content_body = await self._read_frame()
            if body_channel != channel or not isinstance(
                content_body, body_frames.ContentBody
            ):
                raise ConnectionError(  # noqa: TRY301
                    f'Unexpected returned message body: '
                    f'channel={body_channel}, value={content_body!r}'
                )
            body_size = len(content_body.value)
            if not body_size or body_size > remaining:
                raise ConnectionError(  # noqa: TRY301
                    'Returned message body size does not match its header'
                )
            remaining -= body_size

        self._logger.warning(
            'RabbitMQ returned an unroutable publication: '
            'reply=%s %s exchange=%s routing_key=%s',
            returned.reply_code,
            returned.reply_text,
            returned.exchange,
            returned.routing_key,
        )

    async def _send_heartbeats(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._heartbeat_interval)
                self._writer.write(frame.marshal(heartbeat.Heartbeat(), 0))
                await self._writer.drain()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            self.error = exc
            self._writer.close()
            self._closed.set()

    async def wait_closed(self) -> None:
        await self._closed.wait()

    @property
    def frame_max(self) -> int:
        return self._frame_max

    @property
    def blocked_reason(self) -> str | None:
        return self._blocked_reason

    @property
    def flow_active(self) -> bool:
        return self._flow_active

    async def publish(self, values: tuple[frame.FrameTypes, ...]) -> None:
        for value in values:
            self._writer.write(frame.marshal(value, 1))
        await self._writer.drain()

    async def close(self) -> None:
        if not self._closed.is_set():
            try:
                self._writer.write(
                    frame.marshal(
                        commands.Connection.Close(
                            200, 'Client Requested', 0, 0
                        ),
                        0,
                    )
                )
                await self._writer.drain()
            except Exception as exc:  # noqa: BLE001
                self._logger.debug(
                    'AMQP close frame could not be sent: %s', exc
                )
        self._writer.close()
        with contextlib.suppress(Exception):
            await self._writer.wait_closed()
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._heartbeat_task
        if self._reader_task is not None:
            self._reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader_task
        self._closed.set()


async def connect(url: str) -> Connection:
    return await Connection.connect(url)


def frame_values(  # noqa: PLR0913, PLR0917, FBT001
    exchange: str,
    routing_key: str,
    body: bytes,
    mandatory: bool,  # noqa: FBT001
    properties: commands.Basic.Properties,
    frame_max: int,
) -> tuple[frame.FrameTypes, ...]:
    values: list[frame.FrameTypes] = [
        commands.Basic.Publish(
            exchange=exchange, routing_key=routing_key, mandatory=mandatory
        ),
        header.ContentHeader(body_size=len(body), properties=properties),
    ]
    body_max = max(1, frame_max - 8)
    if body:
        values.extend(
            body_frames.ContentBody(body[offset : offset + body_max])
            for offset in range(0, len(body), body_max)
        )
    return tuple(values)
