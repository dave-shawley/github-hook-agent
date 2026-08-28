import functools
import logging
import typing as t
import unittest

import fastapi
import starlette.types
from fastapi.testclient import TestClient
from fastapi_runner import middleware


class AccessLogRecord(logging.LogRecord):
    status_code: int | str
    bytes_sent: int
    user_agent: str
    client: str
    method: str
    path: str
    http_version: str
    duration: str


class AccessLogMiddlewareTests(unittest.TestCase):
    def test_access_log_records_normal_handler(self) -> None:
        app = fastapi.FastAPI(docs_url=None, openapi_url=None, redoc_url=None)
        app.add_middleware(middleware.AccessLogMiddleware)

        @app.get('/logged')
        def logged() -> dict[str, bool]:
            return {'ok': True}

        with (
            self.assertLogs('api-runner.access', level='INFO') as captured,
            TestClient(app) as client,
        ):
            response = client.get('/logged')

        captured_records = t.cast('list[AccessLogRecord]', captured.records)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(captured.records), 1)
        self.assertEqual(captured_records[0].path, '/logged')
        self.assertEqual(captured_records[0].status_code, 200)

    def test_access_log_skips_decorated_handler(self) -> None:
        app = fastapi.FastAPI(docs_url=None, openapi_url=None, redoc_url=None)
        app.add_middleware(middleware.AccessLogMiddleware)

        @app.get('/health')
        @middleware.disable_access_log
        def health() -> dict[str, bool]:
            return {'ok': True}

        with (
            self.assertNoLogs('api-runner.access', level='INFO'),
            TestClient(app) as client,
        ):
            response = client.get('/health')

        self.assertEqual(response.status_code, 200)

    def test_access_log_skips_ignored_paths(self) -> None:
        app = fastapi.FastAPI()
        app.add_middleware(
            middleware.AccessLogMiddleware,
            ignored_paths={'/docs', '/openapi.json', '/redoc'},
        )

        with (
            self.assertNoLogs('api-runner.access', level='INFO'),
            TestClient(app) as client,
        ):
            response = client.get('/docs')

        self.assertEqual(response.status_code, 200)

    def test_access_log_uses_warning_level_for_4xx_status(self) -> None:
        app = fastapi.FastAPI(docs_url=None, openapi_url=None, redoc_url=None)
        app.add_middleware(middleware.AccessLogMiddleware)

        @app.get('/missing')
        def missing() -> fastapi.Response:
            return fastapi.Response(status_code=404)

        with (
            self.assertLogs('api-runner.access', level='WARNING') as captured,
            TestClient(app) as client,
        ):
            response = client.get('/missing')

        captured_records = t.cast('list[AccessLogRecord]', captured.records)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(len(captured.records), 1)
        self.assertEqual(captured_records[0].levelno, 30)
        self.assertEqual(captured_records[0].status_code, 404)

    def test_access_log_uses_error_level_for_5xx_status(self) -> None:
        app = fastapi.FastAPI(docs_url=None, openapi_url=None, redoc_url=None)
        app.add_middleware(middleware.AccessLogMiddleware)

        @app.get('/error')
        def error() -> fastapi.Response:
            return fastapi.Response(status_code=503)

        with (
            self.assertLogs('api-runner.access', level='ERROR') as captured,
            TestClient(app) as client,
        ):
            response = client.get('/error')

        captured_records = t.cast('list[AccessLogRecord]', captured.records)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(len(captured.records), 1)
        self.assertEqual(captured_records[0].levelno, 40)
        self.assertEqual(captured_records[0].status_code, 503)

    def test_access_log_records_request_metadata_and_body_size(self) -> None:
        app = fastapi.FastAPI(docs_url=None, openapi_url=None, redoc_url=None)
        app.add_middleware(middleware.AccessLogMiddleware)

        @app.get('/payload')
        def payload() -> fastapi.Response:
            return fastapi.Response(content=b'payload')

        with (
            self.assertLogs('api-runner.access', level='INFO') as captured,
            TestClient(app, client=('2001:db8::1', 4321)) as client,
        ):
            response = client.get(
                '/payload', headers={'user-agent': 'custom-agent'}
            )

        captured_records = t.cast('list[AccessLogRecord]', captured.records)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(captured_records[0].bytes_sent, 7)
        self.assertEqual(captured_records[0].client, '[2001:db8::1]:4321')
        self.assertEqual(captured_records[0].user_agent, 'custom-agent')
        self.assertEqual(captured_records[0].method, 'GET')
        self.assertEqual(captured_records[0].http_version, '1.1')
        self.assertRegex(captured_records[0].duration, r'^\d+\.\d{3} ms$')

    def test_access_log_disabled_detects_wrapped_function(self) -> None:
        @middleware.disable_access_log
        def endpoint() -> None:
            return None

        @functools.wraps(endpoint, updated=())
        def wrapped() -> None:
            return endpoint()

        self.assertTrue(
            middleware._is_access_log_disabled(
                {'type': 'http', 'endpoint': wrapped}
            )
        )

    def test_access_log_state_finish_keeps_resolved_disable_decision(
        self,
    ) -> None:
        @middleware.disable_access_log
        def endpoint() -> None:
            return None

        state = middleware._AccessLogState(
            {
                'type': 'http',
                'path': '/resolved',
                'method': 'GET',
                'http_version': '1.1',
                'endpoint': endpoint,
            }
        )

        state.finish(
            {
                'type': 'http',
                'path': '/resolved',
                'method': 'GET',
                'http_version': '1.1',
                'endpoint': lambda: None,
            }
        )

        self.assertTrue(state.access_log_disabled)

    def test_access_log_disabled_detects_bound_method(self) -> None:
        class Handler:
            @middleware.disable_access_log
            def endpoint(self) -> None:
                return None

        self.assertTrue(
            middleware._is_access_log_disabled(
                {'type': 'http', 'endpoint': Handler().endpoint}
            )
        )

    def test_access_log_disabled_returns_false_without_endpoint(self) -> None:
        self.assertFalse(middleware._is_access_log_disabled({'type': 'http'}))


class AccessLogMiddlewareAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_non_http_scope_bypasses_logging(self) -> None:
        calls: list[str] = []
        messages: list[starlette.types.Message] = []

        async def app(
            scope: starlette.types.Scope,
            _receive: starlette.types.Receive,
            send: starlette.types.Send,
        ) -> None:
            calls.append(t.cast('str', scope['type']))
            await send({'type': 'websocket.accept'})

        async def receive() -> dict[str, object]:
            return {'type': 'websocket.connect'}

        async def send(message: starlette.types.Message) -> None:
            messages.append(message)

        layer = middleware.AccessLogMiddleware(app)
        scope = {'type': 'websocket', 'path': '/ws'}

        with self.assertNoLogs('api-runner.access', level='INFO'):
            await layer(scope, receive, send)

        self.assertEqual(calls, ['websocket'])
        self.assertEqual(messages, [{'type': 'websocket.accept'}])

    async def test_access_log_resolution_can_happen_after_endpoint_lookup(
        self,
    ) -> None:
        @middleware.disable_access_log
        def endpoint() -> None:
            return None

        async def app(
            scope: starlette.types.Scope,
            _receive: starlette.types.Receive,
            send: starlette.types.Send,
        ) -> None:
            scope['endpoint'] = endpoint
            await send({'type': 'http.response.start', 'status': 200})
            await send({'type': 'http.response.body', 'body': b'ok'})

        async def receive() -> dict[str, object]:
            return {'type': 'http.request', 'body': b'', 'more_body': False}

        async def send(_message: starlette.types.Message) -> None:
            return None

        layer = middleware.AccessLogMiddleware(app)
        scope = {
            'type': 'http',
            'path': '/late-endpoint',
            'method': 'GET',
            'http_version': '1.1',
        }

        with self.assertNoLogs('api-runner.access', level='INFO'):
            await layer(scope, receive, send)

    async def test_message_logger_logs_error_when_no_status_was_sent(
        self,
    ) -> None:
        scope = {
            'type': 'http',
            'path': '/missing-status',
            'method': 'GET',
            'http_version': '1.1',
        }

        with self.assertLogs('api-runner.access', level='ERROR') as captured:
            async with middleware._message_logger(scope):
                pass

        captured_records = t.cast('list[AccessLogRecord]', captured.records)
        self.assertEqual(len(captured_records), 1)
        self.assertEqual(captured_records[0].levelno, logging.ERROR)
        self.assertEqual(captured_records[0].status_code, '-')
        self.assertEqual(captured_records[0].client, '-')
        self.assertEqual(captured_records[0].user_agent, '-')

    async def test_message_logger_rejects_unexpected_status_code_type(
        self,
    ) -> None:
        scope = {
            'type': 'http',
            'path': '/bad-status',
            'method': 'GET',
            'http_version': '1.1',
        }

        with self.assertRaises(AssertionError):
            async with middleware._message_logger(scope) as state:
                state.data['status_code'] = object()  # type: ignore[invalid-assignment, ty:invalid-assignment]

    async def test_access_log_forwards_unhandled_message_types(self) -> None:
        messages: list[starlette.types.Message] = []

        async def app(
            _scope: starlette.types.Scope,
            _receive: starlette.types.Receive,
            send: starlette.types.Send,
        ) -> None:
            await send({'type': 'http.response.start', 'status': 200})
            await send(
                {'type': 'http.response.trailers', 'more_trailers': False}
            )
            await send({'type': 'http.response.body', 'body': b'data'})

        async def receive() -> starlette.types.Message:
            return {'type': 'http.request', 'body': b'', 'more_body': False}

        async def send(message: starlette.types.Message) -> None:
            messages.append(message)

        layer = middleware.AccessLogMiddleware(app)
        scope = {
            'type': 'http',
            'path': '/trailers',
            'method': 'GET',
            'http_version': '1.1',
        }

        with self.assertLogs('api-runner.access', level='INFO') as captured:
            await layer(scope, receive, send)

        captured_records = t.cast('list[AccessLogRecord]', captured.records)
        self.assertEqual(messages[1]['type'], 'http.response.trailers')
        self.assertEqual(captured_records[0].bytes_sent, 4)
