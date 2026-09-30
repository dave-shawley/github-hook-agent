from collections import abc

import fastapi
from fastapi_runner import lifespan

from github_webhook import rabbitmq, webhook


def configure(app: fastapi.FastAPI) -> None:
    app.include_router(webhook.router)


def lifespans() -> abc.Generator[lifespan.LifespanHook]:
    yield rabbitmq.rabbitmq_lifespan
