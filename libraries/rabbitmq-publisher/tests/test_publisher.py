import asyncio
import typing as t
import unittest
from unittest import mock

import pydantic
from pamqp import body, commands, frame, header, heartbeat
from rabbitmq_publisher import api, connection


class FakeReader:
    def __init__(self, data: bytes) -> None:
        self._data = bytearray(data)
        self._changed = asyncio.Event()
        self._eof = False

    async def readexactly(self, size: int) -> bytes:
        while len(self._data) < size and not self._eof:
            self._changed.clear()
            await self._changed.wait()
        if len(self._data) < size:
            raise asyncio.IncompleteReadError(bytes(self._data), size)
        result = bytes(self._data[:size])
        del self._data[:size]
        return result

    def feed_eof(self) -> None:
        self._eof = True
        self._changed.set()

    def feed(self, data: bytes) -> None:
        self._data.extend(data)
        self._changed.set()


class FakeWriter:
    def __init__(self, reader: FakeReader) -> None:
        self.reader = reader
        self.writes: list[bytes] = []
        self.fail_on_drain = False
        self.drain_started = asyncio.Event()

    def write(self, data: bytes) -> None:
        self.writes.append(data)

    async def drain(self) -> None:
        self.drain_started.set()
        if self.fail_on_drain:
            raise ConnectionError('write failed')

    def close(self) -> None:
        self.reader.feed_eof()

    async def wait_closed(self) -> None:
        return


class ErroredConnection:
    def __init__(self) -> None:
        self.error: BaseException | None = ConnectionError('connection failed')
        self.blocked_reason: str | None = None
        self.flow_active = True
        self.frame_max = 131072
        self._closed = asyncio.Event()

    async def wait_closed(self) -> None:
        await self._closed.wait()

    async def publish(self, _values: object) -> None:
        raise AssertionError('errored connections must not publish')

    async def close(self) -> None:
        self._closed.set()


def fake_broker() -> tuple[FakeReader, FakeWriter]:
    frames = [
        frame.marshal(
            commands.Connection.Start(
                server_properties={}, mechanisms='PLAIN', locales='en_US'
            ),
            0,
        ),
        frame.marshal(commands.Connection.Tune(0, 131072, 0), 0),
        frame.marshal(commands.Connection.OpenOk(), 0),
        frame.marshal(commands.Channel.OpenOk(), 1),
    ]
    reader = FakeReader(b''.join(frames))
    return reader, FakeWriter(reader)


def published_frames(writer: FakeWriter) -> list[object]:
    result: list[object] = []
    for data in writer.writes:
        _, _, value = frame.unmarshal(data)
        result.append(value)
    return result


class PublisherTests(unittest.IsolatedAsyncioTestCase):
    def test_constructor_rejects_invalid_urls(self) -> None:
        invalid_urls = (
            'http://guest:guest@localhost',
            'amqp://guest:guest@',
            'amqp://guest:guest@localhost:not-a-port',
            'amqp://guest:guest@localhost?heartbeat=not-an-int',
            'amqp://guest:guest@localhost?connection_timeout=not-a-number',
            'amqp://guest:guest@localhost?connection_timeout=0',
        )
        for url in invalid_urls:
            with self.subTest(url=url), self.assertRaises(ValueError):
                api.Publisher(url)

    async def test_context_cannot_be_started_twice(self) -> None:
        async with api.Publisher('amqp://guest:guest@localhost') as publisher:
            with self.assertRaises(api.PublisherClosed):
                await publisher.__aenter__()

    async def test_publisher_can_be_closed_before_it_is_started(self) -> None:
        publisher = api.Publisher('amqp://guest:guest@localhost')

        await publisher.__aexit__(None, None, None)

        with self.assertRaises(api.PublisherClosed):
            await publisher.publish(message_body=b'hello')

    async def test_publish_reports_an_errored_connection(self) -> None:
        current = ErroredConnection()

        async def connect(*_args: object, **_kwargs: object) -> object:
            return current

        with mock.patch(
            'rabbitmq_publisher.publisher.connection.connect', connect
        ):
            async with api.Publisher(
                'amqp://guest:guest@localhost'
            ) as publisher:
                await asyncio.sleep(0)
                with self.assertRaises(api.PublisherUnavailable) as raised:
                    await publisher.publish(message_body=b'hello')

        self.assertIsNone(raised.exception.__cause__)

    async def test_publish_preserves_the_previous_connection_error(
        self,
    ) -> None:
        current = ErroredConnection()
        attempts = 0

        async def connect(*_args: object, **_kwargs: object) -> object:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise ConnectionError('initial connection failed')
            return current

        with (
            mock.patch(
                'rabbitmq_publisher.publisher.connection.connect', connect
            ),
            mock.patch(
                'rabbitmq_publisher.publisher.random.uniform',
                return_value=0.001,
            ),
        ):
            async with api.Publisher(
                'amqp://guest:guest@localhost'
            ) as publisher:
                with self.assertRaises(api.PublisherUnavailable) as raised:
                    async with asyncio.timeout(1):
                        await publisher.publish(message_body=b'hello')

        self.assertIsInstance(raised.exception.__cause__, ConnectionError)
        self.assertEqual(
            str(raised.exception.__cause__), 'initial connection failed'
        )

    async def test_publish_timeout_is_owned_by_caller(self) -> None:
        async def blocked_connection(
            *_args: object, **_kwargs: object
        ) -> object:
            await asyncio.Future()

        with mock.patch('asyncio.open_connection', blocked_connection):
            async with api.Publisher(
                'amqp://guest:guest@localhost'
            ) as publisher:
                with self.assertRaises(TimeoutError):
                    async with asyncio.timeout(0.01):
                        await publisher.publish(message_body=b'hello')

    async def test_publish_rejects_invalid_arguments(self) -> None:
        publisher = api.Publisher('amqp://guest:guest@localhost')
        invalid_calls: tuple[dict[str, object], ...] = (
            {'exchange': 1},
            {'exchange': 'bad/name'},
            {'routing_key': 1},
            {'routing_key': 'x' * 257},
            {'message_body': 1},
            {'mandatory': object()},
            {'app_id': 1},
            {'app_id': 'x' * 257},
            {'delivery_mode': object()},
            {'delivery_mode': 3},
            {'headers': []},
            {'priority': object()},
            {'priority': 256},
            {'timestamp': 'now'},
        )
        for kwargs in invalid_calls:
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(api.ParameterError) as raised:
                    await publisher.publish(**kwargs)  # type: ignore[bad-argument-type,ty:invalid-argument-type]
                self.assertIsInstance(
                    raised.exception.validation_error,
                    pydantic.ValidationError,
                )

    async def test_context_entry_does_not_wait_for_connection(self) -> None:
        connection_started = asyncio.Event()
        release_connection = asyncio.Event()

        async def open_connection(*_args: object, **_kwargs: object) -> None:
            connection_started.set()
            await release_connection.wait()

        with mock.patch('asyncio.open_connection', open_connection):
            async with api.Publisher('amqp://guest:guest@localhost'):
                await asyncio.wait_for(connection_started.wait(), 0.1)

            release_connection.set()

    async def test_publish_waits_for_connection_and_writes_message(
        self,
    ) -> None:
        reader, writer = fake_broker()

        async def open_connection(*_args: object, **_kwargs: object) -> object:
            return reader, writer

        with mock.patch('asyncio.open_connection', open_connection):
            async with api.Publisher(
                'amqp://guest:guest@localhost'
            ) as publisher:
                result = await publisher.publish(
                    exchange='events',
                    routing_key='webhook',
                    message_body='hello',
                    content_type='text/plain',
                )

        self.assertIsNone(result)
        values = published_frames(writer)
        publish = next(
            value
            for value in values
            if isinstance(value, commands.Basic.Publish)
        )
        self.assertEqual(publish.exchange, 'events')
        self.assertEqual(publish.routing_key, 'webhook')
        content_header = next(
            value
            for value in values
            if isinstance(value, header.ContentHeader)
        )
        self.assertEqual(content_header.properties.content_type, 'text/plain')

    async def test_publish_supports_tls_urls_and_empty_bodies(self) -> None:
        reader, writer = fake_broker()

        async def open_connection(*_args: object, **kwargs: object) -> object:
            self.assertTrue(kwargs['ssl'])
            self.assertEqual(kwargs['server_hostname'], 'localhost')
            return reader, writer

        with mock.patch('asyncio.open_connection', open_connection):
            async with api.Publisher(
                'amqps://guest:guest@localhost'
            ) as publisher:
                await publisher.publish(message_body=b'')

        values = published_frames(writer)
        self.assertTrue(
            any(isinstance(value, commands.Basic.Publish) for value in values)
        )
        self.assertTrue(
            any(isinstance(value, header.ContentHeader) for value in values)
        )

    async def test_broker_heartbeat_is_echoed(self) -> None:
        reader, writer = fake_broker()

        async def open_connection(*_args: object, **_kwargs: object) -> object:
            return reader, writer

        with mock.patch('asyncio.open_connection', open_connection):
            async with api.Publisher(
                'amqp://guest:guest@localhost'
            ) as publisher:
                await publisher.publish(message_body=b'hello')
                writer.drain_started.clear()
                reader.feed(frame.marshal(heartbeat.Heartbeat(), 0))
                await asyncio.wait_for(writer.drain_started.wait(), 0.1)

        values = published_frames(writer)
        self.assertTrue(
            any(isinstance(value, heartbeat.Heartbeat) for value in values)
        )

    async def test_publisher_heartbeats_and_handles_heartbeat_write_failure(
        self,
    ) -> None:
        reader, writer = fake_broker()

        async def open_connection(*_args: object, **_kwargs: object) -> object:
            return reader, writer

        with mock.patch('asyncio.open_connection', open_connection):
            async with api.Publisher(
                'amqp://guest:guest@localhost?heartbeat=1'
            ) as publisher:
                await publisher.publish(message_body=b'hello')
                writer.drain_started.clear()
                writer.fail_on_drain = True
                await asyncio.wait_for(writer.drain_started.wait(), 1.1)
                await asyncio.sleep(0)

        self.assertTrue(writer.reader._eof)

    async def test_broker_close_is_acknowledged(self) -> None:
        reader, writer = fake_broker()

        async def open_connection(*_args: object, **_kwargs: object) -> object:
            return reader, writer

        close = frame.marshal(
            commands.Connection.Close(320, 'closed', 0, 0), 0
        )
        with mock.patch('asyncio.open_connection', open_connection):
            async with api.Publisher(
                'amqp://guest:guest@localhost'
            ) as publisher:
                await publisher.publish(message_body=b'hello')
                reader.feed(close)
                await asyncio.sleep(0)
                await asyncio.sleep(0)

        values = published_frames(writer)
        self.assertTrue(
            any(
                isinstance(value, commands.Connection.CloseOk)
                for value in values
            )
        )

    async def test_unexpected_connection_frame_closes_connection(self) -> None:
        reader, writer = fake_broker()

        async def open_connection(*_args: object, **_kwargs: object) -> object:
            return reader, writer

        with mock.patch('asyncio.open_connection', open_connection):
            async with api.Publisher(
                'amqp://guest:guest@localhost'
            ) as publisher:
                await publisher.publish(message_body=b'hello')
                reader.feed(frame.marshal(commands.Connection.OpenOk(), 0))
                await asyncio.sleep(0)
                await asyncio.sleep(0)

        self.assertTrue(writer.reader._eof)

    async def test_unexpected_channel_frame_closes_connection(self) -> None:
        reader, writer = fake_broker()

        async def open_connection(*_args: object, **_kwargs: object) -> object:
            return reader, writer

        with mock.patch('asyncio.open_connection', open_connection):
            async with api.Publisher(
                'amqp://guest:guest@localhost'
            ) as publisher:
                await publisher.publish(message_body=b'hello')
                reader.feed(frame.marshal(commands.Channel.OpenOk(), 1))
                await asyncio.sleep(0)
                await asyncio.sleep(0)

        self.assertTrue(writer.reader._eof)

    async def test_unexpected_channel_number_closes_connection(self) -> None:
        reader, writer = fake_broker()

        async def open_connection(*_args: object, **_kwargs: object) -> object:
            return reader, writer

        with mock.patch('asyncio.open_connection', open_connection):
            async with api.Publisher(
                'amqp://guest:guest@localhost'
            ) as publisher:
                await publisher.publish(message_body=b'hello')
                reader.feed(frame.marshal(commands.Channel.OpenOk(), 2))
                await asyncio.sleep(0)
                await asyncio.sleep(0)

        self.assertTrue(writer.reader._eof)

    async def test_broker_channel_close_is_acknowledged(self) -> None:
        reader, writer = fake_broker()

        async def open_connection(*_args: object, **_kwargs: object) -> object:
            return reader, writer

        close = frame.marshal(
            commands.Channel.Close(404, 'exchange not found', 60, 40), 1
        )
        with mock.patch('asyncio.open_connection', open_connection):
            async with api.Publisher(
                'amqp://guest:guest@localhost'
            ) as publisher:
                await publisher.publish(message_body=b'hello')
                writer.drain_started.clear()
                reader.feed(close)
                await asyncio.wait_for(writer.drain_started.wait(), 0.1)

        values = published_frames(writer)
        self.assertTrue(
            any(
                isinstance(value, commands.Channel.CloseOk) for value in values
            )
        )

    async def test_mandatory_return_is_consumed_and_logged(self) -> None:
        reader, writer = fake_broker()

        async def open_connection(*_args: object, **_kwargs: object) -> object:
            return reader, writer

        returned = b''.join(
            (
                frame.marshal(
                    commands.Basic.Return(
                        312, 'no route', 'events', 'missing'
                    ),
                    1,
                ),
                frame.marshal(
                    header.ContentHeader(
                        body_size=3, properties=commands.Basic.Properties()
                    ),
                    1,
                ),
                frame.marshal(body.ContentBody(b'hey'), 1),
            )
        )
        with mock.patch('asyncio.open_connection', open_connection):
            async with api.Publisher(
                'amqp://guest:guest@localhost'
            ) as publisher:
                await publisher.publish(
                    exchange='events',
                    routing_key='missing',
                    message_body=b'hey',
                    mandatory=True,
                )
                with self.assertLogs(
                    'rabbitmq_publisher.connection', level='WARNING'
                ) as logs:
                    reader.feed(returned)
                    await asyncio.sleep(0)
                    await asyncio.sleep(0)

        self.assertIn('events', logs.output[0])
        self.assertIn('missing', logs.output[0])

    async def test_returned_message_rejects_an_invalid_header(self) -> None:
        reader, writer = fake_broker()

        async def open_connection(*_args: object, **_kwargs: object) -> object:
            return reader, writer

        returned = b''.join(
            (
                frame.marshal(
                    commands.Basic.Return(
                        312, 'no route', 'events', 'missing'
                    ),
                    1,
                ),
                frame.marshal(commands.Basic.Publish(), 1),
            )
        )
        with mock.patch('asyncio.open_connection', open_connection):
            async with api.Publisher(
                'amqp://guest:guest@localhost'
            ) as publisher:
                await publisher.publish(message_body=b'hello')
                reader.feed(returned)
                await asyncio.sleep(0)
                await asyncio.sleep(0)

        self.assertTrue(writer.reader._eof)

    async def test_returned_message_rejects_an_invalid_body_frame(
        self,
    ) -> None:
        reader, writer = fake_broker()

        async def open_connection(*_args: object, **_kwargs: object) -> object:
            return reader, writer

        returned = b''.join(
            (
                frame.marshal(
                    commands.Basic.Return(
                        312, 'no route', 'events', 'missing'
                    ),
                    1,
                ),
                frame.marshal(
                    header.ContentHeader(
                        body_size=3, properties=commands.Basic.Properties()
                    ),
                    1,
                ),
                frame.marshal(commands.Channel.Flow(active=True), 1),
            )
        )
        with mock.patch('asyncio.open_connection', open_connection):
            async with api.Publisher(
                'amqp://guest:guest@localhost'
            ) as publisher:
                await publisher.publish(message_body=b'hello')
                reader.feed(returned)
                await asyncio.sleep(0)
                await asyncio.sleep(0)

        self.assertTrue(writer.reader._eof)

    async def test_returned_message_rejects_a_body_size_mismatch(self) -> None:
        reader, writer = fake_broker()

        async def open_connection(*_args: object, **_kwargs: object) -> object:
            return reader, writer

        returned = b''.join(
            (
                frame.marshal(
                    commands.Basic.Return(
                        312, 'no route', 'events', 'missing'
                    ),
                    1,
                ),
                frame.marshal(
                    header.ContentHeader(
                        body_size=3, properties=commands.Basic.Properties()
                    ),
                    1,
                ),
                frame.marshal(body.ContentBody(b'too long'), 1),
            )
        )
        with mock.patch('asyncio.open_connection', open_connection):
            async with api.Publisher(
                'amqp://guest:guest@localhost'
            ) as publisher:
                await publisher.publish(message_body=b'hello')
                reader.feed(returned)
                await asyncio.sleep(0)
                await asyncio.sleep(0)

        self.assertTrue(writer.reader._eof)

    async def test_blocked_connection_rejects_publishes_until_unblocked(
        self,
    ) -> None:
        reader, writer = fake_broker()

        async def open_connection(*_args: object, **_kwargs: object) -> object:
            return reader, writer

        with mock.patch('asyncio.open_connection', open_connection):
            async with api.Publisher(
                'amqp://guest:guest@localhost'
            ) as publisher:
                await publisher.publish(message_body=b'first')
                reader.feed(
                    frame.marshal(commands.Connection.Blocked('memory'), 0)
                )
                await asyncio.sleep(0)
                await asyncio.sleep(0)
                with self.assertRaisesRegex(
                    api.PublisherUnavailable, 'blocked: memory'
                ):
                    await publisher.publish(message_body=b'blocked')

                reader.feed(frame.marshal(commands.Connection.Unblocked(), 0))
                await asyncio.sleep(0)
                await asyncio.sleep(0)
                await publisher.publish(message_body=b'second')

    async def test_channel_flow_rejects_publishes_until_flow_resumes(
        self,
    ) -> None:
        reader, writer = fake_broker()

        async def open_connection(*_args: object, **_kwargs: object) -> object:
            return reader, writer

        with mock.patch('asyncio.open_connection', open_connection):
            async with api.Publisher(
                'amqp://guest:guest@localhost'
            ) as publisher:
                await publisher.publish(message_body=b'first')
                writer.drain_started.clear()
                reader.feed(
                    frame.marshal(commands.Channel.Flow(active=False), 1)
                )
                await asyncio.wait_for(writer.drain_started.wait(), 0.1)
                with self.assertRaisesRegex(
                    api.PublisherUnavailable, 'flow control'
                ):
                    await publisher.publish(message_body=b'blocked')

                writer.drain_started.clear()
                reader.feed(
                    frame.marshal(commands.Channel.Flow(active=True), 1)
                )
                await asyncio.wait_for(writer.drain_started.wait(), 0.1)
                await publisher.publish(message_body=b'second')

        values = published_frames(writer)
        flow_acks = [
            value
            for value in values
            if isinstance(value, commands.Channel.FlowOk)
        ]
        self.assertEqual([value.active for value in flow_acks], [False, True])

    async def test_failed_handshake_closes_transport(self) -> None:
        reader = FakeReader(
            frame.marshal(commands.Connection.Close(403, 'denied', 0, 0), 0)
        )
        writer = FakeWriter(reader)

        async def open_connection(*_args: object, **_kwargs: object) -> object:
            return reader, writer

        with mock.patch('asyncio.open_connection', open_connection):
            async with api.Publisher('amqp://guest:guest@localhost'):
                await asyncio.sleep(0)

        self.assertTrue(writer.reader._eof)

    async def test_handshake_rejects_unexpected_frame(self) -> None:
        reader = FakeReader(
            frame.marshal(commands.Connection.Tune(0, 131072, 0), 0)
        )
        writer = FakeWriter(reader)

        async def open_connection(*_args: object, **_kwargs: object) -> object:
            return reader, writer

        with mock.patch('asyncio.open_connection', open_connection):
            async with api.Publisher('amqp://guest:guest@localhost'):
                await asyncio.sleep(0)

        self.assertTrue(writer.reader._eof)

    async def test_connection_close_without_background_tasks_closes_transport(
        self,
    ) -> None:
        reader = FakeReader(b'')
        writer = FakeWriter(reader)
        managed = connection.Connection(
            t.cast('asyncio.StreamReader', reader),
            t.cast('asyncio.StreamWriter', writer),
            0,
        )

        self.assertEqual(managed.frame_max, 131072)
        await managed.close()

        self.assertTrue(writer.reader._eof)

    async def test_publish_after_shutdown_uses_stable_closed_error(
        self,
    ) -> None:
        async with api.Publisher('amqp://guest:guest@localhost') as publisher:
            pass

        with self.assertRaises(api.PublisherClosed):
            await publisher.publish(message_body=b'hello')

    async def test_publish_write_failure_uses_stable_unavailable_error(
        self,
    ) -> None:
        reader, writer = fake_broker()

        async def open_connection(*_args: object, **_kwargs: object) -> object:
            return reader, writer

        with mock.patch('asyncio.open_connection', open_connection):
            async with api.Publisher(
                'amqp://guest:guest@localhost'
            ) as publisher:
                await publisher.publish(message_body=b'first')
                writer.fail_on_drain = True
                with self.assertRaises(api.PublisherUnavailable) as raised:
                    await publisher.publish(message_body=b'second')

        self.assertIsInstance(raised.exception.__cause__, ConnectionError)

    async def test_shutdown_unblocks_publish_waiting_for_connection(
        self,
    ) -> None:
        async def blocked_connection(
            *_args: object, **_kwargs: object
        ) -> object:
            await asyncio.Future()

        with mock.patch('asyncio.open_connection', blocked_connection):
            publisher = api.Publisher('amqp://guest:guest@localhost')
            async with publisher:
                pending = asyncio.create_task(
                    publisher.publish(message_body=b'hello')
                )
                await asyncio.sleep(0)

            with self.assertRaises(api.PublisherClosed):
                await pending

    async def test_connection_failures_are_retried(self) -> None:
        reader, writer = fake_broker()
        attempts = 0

        async def open_connection(*_args: object, **_kwargs: object) -> object:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise ConnectionError('broker unavailable')
            return reader, writer

        with (
            mock.patch('asyncio.open_connection', open_connection),
            mock.patch(
                'rabbitmq_publisher.publisher.random.uniform',
                return_value=0.001,
            ),
        ):
            async with api.Publisher(
                'amqp://guest:guest@localhost'
            ) as publisher:
                async with asyncio.timeout(1):
                    await publisher.publish(message_body=b'hello')

        self.assertEqual(attempts, 2)
