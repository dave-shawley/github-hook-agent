import os
import typing as t
from collections import abc
from importlib import metadata

import fastapi.middleware.cors
import pydantic_settings

from fastapi_runner import lifespan, middleware


class CORSSettings(pydantic_settings.BaseSettings):
    model_config = {'env_prefix': 'CORS_'}
    allow_credentials: bool = False
    allow_headers: list[str] = []
    allow_methods: list[str] = [
        'DELETE',
        'GET',
        'HEAD',
        'OPTIONS',
        'POST',
        'PUT',
    ]
    allow_origins: list[str] = []
    allow_private_network: bool = False
    expose_headers: list[str] = []
    max_age: int = 600


ConfigHook = abc.Callable[[fastapi.FastAPI], None]
LifespanGenerator = abc.Callable[[], abc.Generator[lifespan.LifespanHook]]


class ApplicationConfigurationError(RuntimeError):
    pass


def app_factory() -> fastapi.FastAPI:
    span = lifespan.Lifespan()

    try:
        application = os.environ['APPLICATION']
    except KeyError:
        raise ApplicationConfigurationError(
            'APPLICATION environment variable is required'
        ) from None

    try:
        distribution = metadata.distribution(application)
    except metadata.PackageNotFoundError:
        raise ApplicationConfigurationError(
            f'APPLICATION={application!r} does not identify an installed '
            'Python distribution'
        ) from None

    entry_points = distribution.entry_points.select(group='fastapi_runner')
    num_eps = len(entry_points.select(name='configure'))
    if num_eps != 1:
        raise ValueError(
            f'Expected exactly one configure entrypoint in fastapi_runner'
            f' group, but found {num_eps} in {application!r}'
        )

    try:
        gen_lifespans = t.cast(
            'LifespanGenerator', entry_points['lifespans'].load()
        )
    except KeyError:
        pass
    else:
        for hook in gen_lifespans():
            span.add_lifespan(hook)

    cors_settings = CORSSettings()

    app = fastapi.FastAPI(lifespan=span)
    app.add_middleware(
        fastapi.middleware.cors.CORSMiddleware,
        allow_credentials=cors_settings.allow_credentials,
        allow_headers=cors_settings.allow_headers,
        allow_methods=cors_settings.allow_methods,
        allow_origins=cors_settings.allow_origins,
        allow_private_network=cors_settings.allow_private_network,
        expose_headers=cors_settings.expose_headers,
        max_age=cors_settings.max_age,
    )

    # The AccessLogMiddleware should ALWAYS be added last
    app.add_middleware(
        middleware.AccessLogMiddleware,
        ignored_paths=('/docs', '/openapi.json', '/redoc'),
    )

    configure = t.cast('ConfigHook', entry_points['configure'].load())
    configure(app)

    return app
