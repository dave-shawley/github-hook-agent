import contextlib
import pathlib
import typing as t
import urllib.parse
from collections import abc

import fastapi
import pydantic_settings
import rabbitmq_publisher.api
from fastapi_runner import lifespan
from fastapi_utilities import settings


class RabbitConfiguration(pydantic_settings.BaseSettings):
    model_config = {'env_prefix': 'rabbitmq_'}
    exchange: str = 'webhooks'
    url: str | None = None
    host: str = 'rabbitmq'
    port: int = 5672
    user: str = 'github-webhook'
    virtual_host: str = '/'
    password_file: pathlib.Path = pathlib.Path(
        '/run/secrets/rabbitmq-github-webhook-password'
    )


def publisher_url(config: RabbitConfiguration) -> str:
    if config.url is not None:
        return config.url

    try:
        password = config.password_file.read_text().rstrip('\r\n')
    except OSError as exc:
        raise RuntimeError(
            f'Unable to read RabbitMQ password file: {config.password_file}'
        ) from exc
    if not password:
        raise RuntimeError(
            f'RabbitMQ password file is empty: {config.password_file}'
        )

    username = urllib.parse.quote(config.user, safe='')
    encoded_password = urllib.parse.quote(password, safe='')
    encoded_virtual_host = urllib.parse.quote(config.virtual_host, safe='')
    return urllib.parse.urlunsplit(
        (
            'amqp',
            f'{username}:{encoded_password}@{config.host}:{config.port}',
            f'/{encoded_virtual_host}',
            '',
            '',
        )
    )


@contextlib.asynccontextmanager
async def rabbitmq_lifespan() -> abc.AsyncGenerator[
    rabbitmq_publisher.api.Publisher
]:
    config = settings.from_environment(RabbitConfiguration)
    async with rabbitmq_publisher.api.Publisher(
        publisher_url(config)
    ) as publisher:
        yield publisher


async def get_publisher(
    data: lifespan.LifespanMap,
) -> rabbitmq_publisher.api.Publisher:
    return data.get_state(rabbitmq_lifespan)


PublisherDependency = t.Annotated[
    rabbitmq_publisher.api.Publisher, fastapi.Depends(get_publisher)
]
