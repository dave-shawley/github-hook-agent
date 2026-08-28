import contextlib
import typing as t
import unittest
import uuid
from collections import abc

import fastapi.testclient
from fastapi_runner import lifespan


class LifespanTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_arg_function_hook_populates_lifespan_state(self) -> None:
        app = fastapi.FastAPI()
        span = lifespan.Lifespan()
        events: list[str] = []

        @contextlib.asynccontextmanager
        async def hook() -> abc.AsyncGenerator[str]:
            events.append('enter')
            try:
                yield 'function-value'
            finally:
                events.append('exit')

        span.add_lifespan(hook)

        async with span(app) as state:
            self.assertEqual(events, ['enter'])
            self.assertIs(state['lifespan_data'], span)
            self.assertEqual(span[hook], 'function-value')

        self.assertEqual(events, ['enter', 'exit'])

    async def test_function_hook_populates_lifespan_state(self) -> None:
        app = fastapi.FastAPI()
        span = lifespan.Lifespan()
        events: list[str] = []

        @contextlib.asynccontextmanager
        async def hook(
            hook_app: fastapi.FastAPI,
        ) -> abc.AsyncGenerator[str]:
            self.assertIs(hook_app, app)
            events.append('enter')
            try:
                yield 'function-value'
            finally:
                events.append('exit')

        span.add_lifespan(hook)

        async with span(app) as state:
            self.assertEqual(events, ['enter'])
            self.assertIs(state['lifespan_data'], span)
            self.assertEqual(span[hook], 'function-value')

        self.assertEqual(events, ['enter', 'exit'])

    async def test_class_hook_populates_lifespan_state(self) -> None:
        app = fastapi.FastAPI()
        span = lifespan.Lifespan()
        events: list[str] = []
        test_case = self

        class HookContextManager:
            async def __aenter__(self) -> str:
                events.append('enter')
                return 'class-value'

            async def __aexit__(
                self,
                exc_type: type[BaseException] | None,
                exc_value: BaseException | None,
                exc_traceback: object | None,
            ) -> None:
                test_case.assertIsNone(exc_type)
                test_case.assertIsNone(exc_value)
                test_case.assertIsNone(exc_traceback)
                events.append('exit')

        def hook(hook_app: fastapi.FastAPI) -> HookContextManager:
            self.assertIs(hook_app, app)
            return HookContextManager()

        span.add_lifespan(hook)

        async with span(app) as state:
            self.assertEqual(events, ['enter'])
            self.assertIs(state['lifespan_data'], span)
            self.assertEqual(span[hook], 'class-value')

        self.assertEqual(events, ['enter', 'exit'])

    async def test_lifespan_only_enters_each_hook_once(self) -> None:
        app = fastapi.FastAPI()
        span = lifespan.Lifespan()
        events: list[str] = []

        @contextlib.asynccontextmanager
        async def hook(
            hook_app: fastapi.FastAPI,
        ) -> abc.AsyncGenerator[str]:
            self.assertIs(hook_app, app)
            events.append('enter')
            try:
                yield 'value'
            finally:
                events.append('exit')

        span.add_lifespan(hook)

        async with span(app):
            self.assertEqual(events, ['enter'])

        async with span(app):
            self.assertEqual(events, ['enter', 'exit'])
            self.assertEqual(span[hook], 'value')

        self.assertEqual(events, ['enter', 'exit'])

    def test_invoke_hook_rejects_invalid_signature(self) -> None:
        app = fastapi.FastAPI()

        def invalid_hook(_hook_app: fastapi.FastAPI, _extra: object) -> object:
            raise AssertionError('invalid_hook should not be invoked')

        with self.assertRaisesRegex(
            TypeError, r'Invalid lifespan hook signature:'
        ):
            lifespan._invoke_hook(
                t.cast(
                    'lifespan._HookWithoutApp[object] | lifespan._HookWithApp[object]',  # noqa: E501
                    invalid_hook,
                ),
                app,
            )

    def test_add_lifespan_rejects_mutation_after_call(self) -> None:
        app = fastapi.FastAPI()
        span = lifespan.Lifespan()

        @contextlib.asynccontextmanager
        async def original_hook(
            hook_app: fastapi.FastAPI,
        ) -> abc.AsyncGenerator[None]:
            self.assertIs(hook_app, app)
            yield

        @contextlib.asynccontextmanager
        async def late_hook(
            hook_app: fastapi.FastAPI,
        ) -> abc.AsyncGenerator[None]:
            self.assertIs(hook_app, app)
            yield

        span.add_lifespan(original_hook)
        span(app)

        with self.assertRaisesRegex(
            RuntimeError, 'Cannot add lifespan after __call__ has been invoked'
        ):
            span.add_lifespan(late_hook)

    async def test_state_retrieval(self) -> None:
        span = lifespan.Lifespan()

        @contextlib.asynccontextmanager
        async def stateless_hook(
            _hook_app: fastapi.FastAPI,
        ) -> abc.AsyncGenerator[None]:
            yield

        @contextlib.asynccontextmanager
        async def stateful_hook(
            _hook_app: fastapi.FastAPI,
        ) -> abc.AsyncGenerator[uuid.UUID]:
            yield uuid.uuid4()

        @contextlib.asynccontextmanager
        async def unused_hook(
            _hook_app: fastapi.FastAPI,
        ) -> abc.AsyncGenerator[None]:
            yield

        span.add_lifespan(stateless_hook)
        span.add_lifespan(stateful_hook)

        app = fastapi.FastAPI()
        async with span(app):
            self.assertIs(span.get_state(stateless_hook), None)
            self.assertIsInstance(span.get_state(stateful_hook), uuid.UUID)
            with self.assertRaises(fastapi.HTTPException) as cm:
                span.get_state(unused_hook)
            self.assertEqual(
                cm.exception.detail,
                f'Unmet lifespan dependency: {unused_hook!r}',
            )

    async def test_lifespan_map_dependency(self) -> None:
        @contextlib.asynccontextmanager
        async def hook() -> t.AsyncGenerator[uuid.UUID]:
            yield uuid.uuid4()

        span = lifespan.Lifespan()
        span.add_lifespan(hook)
        app = fastapi.FastAPI(lifespan=span)

        @app.get('/')
        async def get_hook_state(data: lifespan.LifespanMap) -> uuid.UUID:
            return data.get_state(hook)

        with fastapi.testclient.TestClient(app) as client:
            async with span(app):
                response = client.get('/')
                self.assertEqual(200, response.status_code)
                self.assertEqual(str(span.get_state(hook)), response.json())

    async def test_unstarted_lifespan(self) -> None:
        @contextlib.asynccontextmanager
        async def hook() -> t.AsyncGenerator[uuid.UUID]:
            yield uuid.uuid4()

        app = fastapi.FastAPI()

        @app.get('/')
        async def get_hook_state(data: lifespan.LifespanMap) -> uuid.UUID:
            return data.get_state(hook)

        with fastapi.testclient.TestClient(app) as client:
            response = client.get('/')
            self.assertEqual(500, response.status_code)
            self.assertEqual(
                'Lifespan not available', response.json()['detail']
            )

    async def test_invalid_lifespan(self) -> None:
        @contextlib.asynccontextmanager
        async def lifespan_hook(
            _app: fastapi.FastAPI,
        ) -> t.AsyncGenerator[dict[str, object]]:
            yield {'lifespan_data': object()}

        app = fastapi.FastAPI(lifespan=lifespan_hook)

        @app.get('/')
        async def get_hook_state(_data: lifespan.LifespanMap) -> uuid.UUID:
            return uuid.uuid4()

        with fastapi.testclient.TestClient(app) as client:
            response = client.get('/')
            self.assertEqual(500, response.status_code)
            self.assertStartsWith(
                response.json()['detail'], 'Unexpected lifespan type: '
            )
