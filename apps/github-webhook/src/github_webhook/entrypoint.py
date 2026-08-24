import fastapi

from github_webhook import webhook


def create_app() -> fastapi.FastAPI:
    app = fastapi.FastAPI()
    app.include_router(webhook.router)
    return app
