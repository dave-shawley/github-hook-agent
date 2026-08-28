import contextlib
import typing as t
import unittest
from collections import abc
from unittest import mock

import fastapi.routing
from fastapi.middleware import cors
from fastapi_runner import entrypoint, lifespan, middleware


def unwrap_as[T](_typ: type[T], value: object | None) -> T:
    if value is None:
        raise AssertionError('Value unexpectedly None')
    return t.cast('T', value)


class FakeEntryPoint:
    def __init__(self, loader: object) -> None:
        self._loader = loader

    def load(self) -> object:
        return self._loader


class FakeEntryPoints:
    def __init__(self, **entry_points: FakeEntryPoint) -> None:
        self._entry_points = entry_points

    def select(self, *, name: str) -> tuple[FakeEntryPoint, ...]:
        try:
            return (self._entry_points[name],)
        except KeyError:
            return ()

    def __getitem__(self, name: str) -> FakeEntryPoint:
        return self._entry_points[name]


class EntryPointTests(unittest.IsolatedAsyncioTestCase):
    def test_app_factory_requires_exactly_one_configure_entry_point(
        self,
    ) -> None:
        entry_points = FakeEntryPoints()

        with (
            mock.patch.object(
                entrypoint.metadata, 'entry_points', return_value=entry_points
            ),
            self.assertRaisesRegex(
                ValueError,
                (
                    'Expected exactly one configure entrypoint in '
                    'fastapi_runner group'
                ),
            ),
        ):
            entrypoint.app_factory()

    def test_app_factory_configures_app_and_adds_middleware(
        self,
    ) -> None:
        configured_apps: list[fastapi.FastAPI] = []

        def configure(app: fastapi.FastAPI) -> None:
            configured_apps.append(app)

            @app.get('/configured')
            def configured() -> dict[str, bool]:
                return {'ok': True}

        entry_points = FakeEntryPoints(configure=FakeEntryPoint(configure))

        with mock.patch.object(
            entrypoint.metadata, 'entry_points', return_value=entry_points
        ):
            app = entrypoint.app_factory()

        self.assertEqual(configured_apps, [app])
        self.assertIn(
            '/configured',
            {
                unwrap_as(fastapi.routing.APIRoute, route).path
                for route in app.routes
            },
        )

        self.assertSetEqual(
            {
                middleware_config.cls
                for middleware_config in app.user_middleware
            },
            {cors.CORSMiddleware, middleware.AccessLogMiddleware},
        )
        for middleware_config in app.user_middleware:
            match middleware_config.cls:
                case cors.CORSMiddleware:
                    settings: abc.Mapping[str, bool | int | list[str]] = {
                        'allow_credentials': False,
                        'allow_headers': [],
                        'allow_origins': [],
                        'allow_private_network': False,
                        'expose_headers': [],
                        'max_age': 600,
                    }
                    for key, value in settings.items():
                        self.assertEqual(
                            middleware_config.kwargs[key],
                            value,
                            f'Unexpected value for {key}',
                        )
                    self.assertSetEqual(
                        set(
                            t.cast(
                                'list[str]',
                                middleware_config.kwargs['allow_methods'],
                            )
                        ),
                        {'DELETE', 'GET', 'HEAD', 'POST', 'PUT', 'OPTIONS'},
                        'Unexpected allow_methods',
                    )
                case middleware.AccessLogMiddleware:
                    self.assertEqual(
                        middleware_config.kwargs['ignored_paths'],
                        ('/docs', '/openapi.json', '/redoc'),
                    )
                case _:
                    self.fail(
                        f'Unexpected middleware class: {middleware_config.cls}'
                    )

    async def test_app_factory_registers_lifespan_hooks(self) -> None:
        entered: list[str] = []
        exited: list[str] = []

        @contextlib.asynccontextmanager
        async def hook(
            app: fastapi.FastAPI,
        ) -> abc.AsyncGenerator[str]:
            entered.append(app.title)
            try:
                yield 'hook-value'
            finally:
                exited.append(app.title)

        def lifespans() -> abc.Generator[entrypoint.lifespan.LifespanHook]:
            yield hook

        def configure(app: fastapi.FastAPI) -> None:
            app.title = 'configured-app'

        entry_points = FakeEntryPoints(
            configure=FakeEntryPoint(configure),
            lifespans=FakeEntryPoint(lifespans),
        )

        with mock.patch.object(
            entrypoint.metadata, 'entry_points', return_value=entry_points
        ):
            app = entrypoint.app_factory()

        async with app.router.lifespan_context(app) as context:
            state = unwrap_as(dict[str, object], context)
            lifespan_data = unwrap_as(
                lifespan.Lifespan, state['lifespan_data']
            )
            self.assertEqual(entered, ['configured-app'])
            self.assertEqual(lifespan_data[hook], 'hook-value')

        self.assertEqual(exited, ['configured-app'])

    async def test_app_factory_allows_missing_lifespan_entry_point(
        self,
    ) -> None:
        configured_apps: list[fastapi.FastAPI] = []

        def configure(app: fastapi.FastAPI) -> None:
            configured_apps.append(app)

        entry_points = FakeEntryPoints(configure=FakeEntryPoint(configure))

        with mock.patch.object(
            entrypoint.metadata, 'entry_points', return_value=entry_points
        ):
            app = entrypoint.app_factory()

        async with app.router.lifespan_context(app) as context:
            state = unwrap_as(dict[str, object], context)
            self.assertEqual(state['lifespan_data'], {})

        self.assertEqual(configured_apps, [app])
