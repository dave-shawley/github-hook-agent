import hmac
import typing as t

import fastapi
import pydantic
import pydantic_settings

from github_hook_agent import utilities

router = fastapi.APIRouter(prefix='/notifications')


class WebhookSettings(pydantic_settings.BaseSettings):
    webhook_secret: pydantic.SecretStr


async def _validate_signature(
    *,
    request: fastapi.Request,
    header: t.Annotated[str, fastapi.Header(alias='x-hub-signature-256')],
) -> None:
    if not header.startswith('sha256='):
        raise fastapi.HTTPException(400)
    expected = header[7:].lower()

    cfg = utilities.from_environment(WebhookSettings)
    digest = hmac.HMAC(
        digestmod='sha256', key=cfg.webhook_secret.get_secret_value().encode()
    )
    body = await request.body()
    digest.update(body)
    actual = digest.hexdigest().lower()
    if actual != expected:
        raise fastapi.HTTPException(400)


WebhookSignature = t.Annotated[
    str,
    fastapi.Depends(_validate_signature),
]


@router.post('/{hook}')
async def process_notification(
    hook: str,  # noqa: ARG001 - name required by path param
    *,
    event: t.Annotated[str, fastapi.Header(alias='x-github-event')],
    hook_id: t.Annotated[str, fastapi.Header(alias='x-github-hook-id')],
    _sig: WebhookSignature,
) -> fastapi.Response:
    logger = utilities.get_logger(process_notification)
    logger.info('Processing %s@%s {hook_id:%s)', event, hook, hook_id)
    return fastapi.Response(status_code=204)
