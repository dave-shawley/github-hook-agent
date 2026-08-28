import fastapi

from github_webhook import webhook


def configure(app: fastapi.FastAPI) -> None:
    app.include_router(webhook.router)
