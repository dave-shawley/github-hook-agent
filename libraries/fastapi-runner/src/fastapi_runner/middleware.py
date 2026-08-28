import contextlib
import inspect
import logging
import time
import typing as t
from collections import abc

import starlette.types

_DISABLE_ACCESS_LOG_ATTR = '_fastapi_runner_disable_access_log'


type _Handler[**P, R] = abc.Callable[P, R]


def disable_access_log[**P, R](handler: _Handler[P, R]) -> _Handler[P, R]:
    setattr(handler, _DISABLE_ACCESS_LOG_ATTR, True)
    return handler


class _LogData(t.TypedDict):
    status_code: int | str
    bytes_sent: int
    user_agent: str
    client: str
    method: str
    path: str
    http_version: str
    duration: t.NotRequired[str]


class _AccessLogState:
    def __init__(self, scope: starlette.types.Scope) -> None:
        self.start_time = time.monotonic_ns()
        self.access_log_disabled = (
            _is_access_log_disabled(scope) if 'endpoint' in scope else False
        )
        self._resolved_access_log_disabled = 'endpoint' in scope
        self.data: _LogData = {
            'status_code': '-',
            'bytes_sent': 0,
            'user_agent': '-',
            'client': '-',
            'method': scope['method'],
            'path': scope['path'],
            'http_version': scope['http_version'],
        }
        if client := scope.get('client', None):
            host: str = client[0]
            port: int = client[1]
            if ':' in host:
                host = f'[{host}]'

            self.data['client'] = f'{host}:{port}'

        if headers := scope.get('headers', None):
            for name, value in headers:
                if name.lower() == b'user-agent':
                    self.data['user_agent'] = value.decode()

    @property
    def status_code(self) -> int | str:
        return self.data['status_code']

    def set_status_code(self, status: int) -> None:
        self.data['status_code'] = status

    def increment_bytes_sent(self, value: int) -> None:
        self.data['bytes_sent'] += value

    def finish(self, scope: starlette.types.Scope) -> None:
        time_ms = (time.monotonic_ns() - self.start_time) * 1e-6
        self.data['duration'] = f'{time_ms:.3f} ms'
        if not self._resolved_access_log_disabled:
            self.access_log_disabled = _is_access_log_disabled(scope)
            self._resolved_access_log_disabled = True


class AccessLogMiddleware:
    def __init__(
        self,
        app: starlette.types.ASGIApp,
        *,
        ignored_paths: abc.Collection[str] = (),
    ) -> None:
        self.app = app
        self.ignored_paths = frozenset(ignored_paths)

    async def __call__(
        self,
        scope: starlette.types.Scope,
        receive: starlette.types.Receive,
        send: starlette.types.Send,
    ) -> None:
        if scope['type'] != 'http' or scope['path'] in self.ignored_paths:
            await self.app(scope, receive, send)
            return

        async with _message_logger(scope) as state:

            async def _send(message: starlette.types.Message) -> None:
                if message['type'] == 'http.response.start':
                    state.set_status_code(message['status'])
                elif message['type'] == 'http.response.body':
                    state.increment_bytes_sent(len(message.get('body', b'')))
                await send(message)

            await self.app(scope, receive, _send)


def _is_access_log_disabled(scope: starlette.types.Scope) -> bool:
    endpoint = scope.get('endpoint', None)
    if endpoint is None:
        return False

    candidates = [endpoint]
    with contextlib.suppress(AttributeError):
        candidates.append(endpoint.__func__)

    for candidate in candidates:
        if getattr(candidate, _DISABLE_ACCESS_LOG_ATTR, False):
            return True
        if getattr(inspect.unwrap(candidate), _DISABLE_ACCESS_LOG_ATTR, False):
            return True

    return False


@contextlib.asynccontextmanager
async def _message_logger(
    scope: starlette.types.Scope,
) -> abc.AsyncGenerator[_AccessLogState]:
    state = _AccessLogState(scope)
    try:
        yield state
    finally:
        state.finish(scope)
        if not state.access_log_disabled:
            match state.status_code:
                case int():
                    if state.status_code >= 500:  # noqa: PLR2004
                        log_level = logging.ERROR
                    elif state.status_code >= 400:  # noqa: PLR2004
                        log_level = logging.WARNING
                    else:
                        log_level = logging.INFO
                case str():
                    log_level = logging.ERROR
                case _:
                    t.assert_never(state.status_code)

            logger = logging.getLogger('api-runner.access')
            adapter = logging.LoggerAdapter(logger, state.data)
            adapter.log(log_level, '')
