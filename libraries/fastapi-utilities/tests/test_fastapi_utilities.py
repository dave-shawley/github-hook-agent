import contextlib
import datetime
import http
import logging
import os
import secrets
import typing as t
import unittest.mock
import zoneinfo
from collections import abc

import fastapi.testclient
import fastapi_utilities
import pydantic
import pydantic_settings
from fastapi_utilities import conditional, dates, settings


class LoggerTarget:
    pass


class UtilityTests(unittest.TestCase):
    def test_get_logger_with_type(self) -> None:
        logger = fastapi_utilities.get_logger(LoggerTarget())
        self.assertEqual(
            logger.name,
            f'{LoggerTarget.__module__}.{LoggerTarget.__name__}',
        )
        self.assertIsInstance(logger, logging.Logger)

    def test_get_logger_with_function(self) -> None:
        def target() -> None:
            return None

        logger = fastapi_utilities.get_logger(target)
        self.assertEqual(logger.name, f'{__name__}.target')

    def test_unwrap(self) -> None:
        obj = object()
        self.assertEqual(fastapi_utilities.unwrap(1), 1)
        self.assertIs(fastapi_utilities.unwrap(obj), obj)

        with self.assertRaises(ValueError):
            fastapi_utilities.unwrap(None)

    def test_unwrap_as(self) -> None:
        obj = object()
        self.assertIs(fastapi_utilities.unwrap_as(obj, object), obj)
        with self.assertRaises(ValueError):
            fastapi_utilities.unwrap_as(None, int)
        with self.assertRaises(TypeError):
            fastapi_utilities.unwrap_as(obj, int)


class DateRelatedTests(unittest.TestCase):
    def test_http_date(self) -> None:
        now = datetime.datetime.now(datetime.UTC)
        expected = now.strftime('%a, %d %b %Y %H:%M:%S GMT')
        self.assertEqual(dates.http_date(now), expected)
        self.assertEqual(dates.http_date(now.timestamp()), expected)
        tz = zoneinfo.ZoneInfo('America/New_York')
        self.assertEqual(dates.http_date(now.astimezone(tz)), expected)
        with self.assertRaises(TypeError):
            dates.http_date('not a timestamp')  # type: ignore[ty:invalid-argument-type]

    def test_parse_http_date(self) -> None:
        now = datetime.datetime.now(datetime.UTC)
        self.assertEqual(
            dates.parse_http_date(dates.http_date(now)),
            now.replace(microsecond=0),
        )

        with self.assertRaises(ValueError):
            dates.parse_http_date('not a date')
        with self.assertRaises(ValueError):
            dates.parse_http_date(0)  # type: ignore[ty:invalid-argument-type]

    def test_utcnow(self) -> None:
        self.assertEqual(dates.utcnow().tzinfo, datetime.UTC)


class ConditionRequestTests(unittest.TestCase):
    def test_generate_entity_headers_without_etag(self) -> None:
        self.assertEqual({}, conditional.generate_entity_headers())

    def test_if_modified_since(self) -> None:
        now = dates.utcnow().replace(microsecond=0)

        for method in ('GET', 'HEAD'):
            request = fastapi.Request(
                scope={
                    'headers': [
                        (
                            b'if-modified-since',
                            now.strftime('%a, %d %b %Y %H:%M:%S GMT').encode(
                                'ascii'
                            ),
                        ),
                    ],
                    'method': method,
                    'type': 'http',
                }
            )
            for variant in (now, now.timestamp(), dates.http_date(now)):
                with self.assertRaises(fastapi.HTTPException) as ctx:
                    conditional.evaluate_conditional_request(
                        request,
                        last_modified=variant,
                    )
                self.assertEqual(
                    ctx.exception.status_code,
                    http.HTTPStatus.NOT_MODIFIED,
                    f'Variant {variant!r}',
                )
                headers = fastapi_utilities.unwrap(ctx.exception.headers)
                self.assertNotIn('etag', headers)
                self.assertEqual(
                    headers['last-modified'], dates.http_date(now)
                )

    def test_if_modified_since_uses_http_date_precision(self) -> None:
        now = dates.utcnow().replace(microsecond=0)
        request = fastapi.Request(
            scope={
                'headers': [
                    (
                        b'if-modified-since',
                        dates.http_date(now).encode('ascii'),
                    ),
                ],
                'method': 'GET',
                'type': 'http',
            }
        )

        with self.assertRaises(fastapi.HTTPException) as ctx:
            conditional.evaluate_conditional_request(
                request,
                last_modified=now + datetime.timedelta(microseconds=1),
            )
        self.assertEqual(
            http.HTTPStatus.NOT_MODIFIED,
            ctx.exception.status_code,
        )

    def test_if_modified_since_ignores_other_methods(self) -> None:
        now = dates.utcnow()
        request = fastapi.Request(
            scope={
                'headers': [
                    (
                        b'if-modified-since',
                        now.strftime('%a, %d %b %Y %H:%M:%S GMT').encode(
                            'ascii'
                        ),
                    ),
                ],
                'method': 'POST',
                'type': 'http',
            }
        )

        conditional.evaluate_conditional_request(
            request,
            last_modified=now,
        )

    def test_invalid_if_modified_since_is_ignored(self) -> None:
        now = dates.utcnow()
        request = fastapi.Request(
            scope={
                'headers': [(b'if-modified-since', b'not-a-date')],
                'method': 'GET',
                'type': 'http',
            }
        )

        conditional.evaluate_conditional_request(
            request,
            last_modified=now,
        )

    def test_multiple_if_modified_since_headers_are_ignored(self) -> None:
        now = dates.utcnow()
        request = fastapi.Request(
            scope={
                'headers': [
                    (
                        b'if-modified-since',
                        dates.http_date(now).encode('ascii'),
                    ),
                    (b'if-modified-since', b'invalid'),
                ],
                'method': 'GET',
                'type': 'http',
            }
        )

        conditional.evaluate_conditional_request(
            request,
            last_modified=now,
        )

    def test_if_modified_since_with_incorrect_type(self) -> None:
        now = dates.utcnow()
        request = fastapi.Request(
            scope={
                'headers': [
                    (
                        b'if-modified-since',
                        now.strftime('%a, %d %b %Y %H:%M:%S GMT').encode(
                            'ascii'
                        ),
                    ),
                ],
                'method': 'GET',
                'type': 'http',
            }
        )
        with self.assertRaises(TypeError):
            conditional.evaluate_conditional_request(
                request,
                last_modified=object(),  # type: ignore[ty:invalid-argument-type]
            )

    def test_if_unmodified_since(self) -> None:
        now = dates.utcnow()
        before = now - datetime.timedelta(seconds=1)
        request = fastapi.Request(
            scope={
                'headers': [
                    (
                        b'if-unmodified-since',
                        dates.http_date(before).encode('ascii'),
                    ),
                ],
                'method': 'POST',
                'type': 'http',
            }
        )
        with self.assertRaises(fastapi.HTTPException) as ctx:
            conditional.evaluate_conditional_request(
                request,
                last_modified=now,
            )
        self.assertEqual(
            http.HTTPStatus.PRECONDITION_FAILED,
            ctx.exception.status_code,
        )

    def test_if_unmodified_since_ignored_when_if_match_is_present(
        self,
    ) -> None:
        now = dates.utcnow()
        before = now - datetime.timedelta(days=1)
        request = fastapi.Request(
            scope={
                'headers': [
                    (b'if-match', b'*'),
                    (
                        b'if-unmodified-since',
                        dates.http_date(before).encode('ascii'),
                    ),
                ],
                'method': 'POST',
                'type': 'http',
            }
        )

        conditional.evaluate_conditional_request(
            request,
            last_modified=now,
        )

    def test_invalid_if_unmodified_since_is_ignored(self) -> None:
        now = dates.utcnow()
        request = fastapi.Request(
            scope={
                'headers': [(b'if-unmodified-since', b'not-a-date')],
                'method': 'POST',
                'type': 'http',
            }
        )

        conditional.evaluate_conditional_request(
            request,
            last_modified=now,
        )

    def test_if_match_uses_strong_comparison(self) -> None:
        etag = secrets.token_hex(16)
        request = fastapi.Request(
            scope={
                'method': 'PUT',
                'type': 'http',
                'headers': [(b'if-match', f'"{etag}"'.encode('ascii'))],
            }
        )
        conditional.evaluate_conditional_request(
            request,
            etag=etag,
        )

        for variant in (f'W/"{etag}"', f'"different-{etag}"'):
            request = fastapi.Request(
                scope={
                    'method': 'PUT',
                    'type': 'http',
                    'headers': [(b'if-match', variant.encode('ascii'))],
                }
            )
            with self.assertRaises(fastapi.HTTPException) as ctx:
                conditional.evaluate_conditional_request(
                    request,
                    etag=etag,
                )
            self.assertEqual(
                http.HTTPStatus.PRECONDITION_FAILED,
                ctx.exception.status_code,
                f'Variant {variant!r}',
            )
            headers = fastapi_utilities.unwrap(ctx.exception.headers)
            self.assertEqual(headers['etag'], f'"{etag}"')
            self.assertNotIn('last-modified', headers)

        wildcard = fastapi.Request(
            scope={
                'method': 'PUT',
                'type': 'http',
                'headers': [(b'if-match', b'*')],
            }
        )
        conditional.evaluate_conditional_request(wildcard, etag=etag)

    def test_if_match_without_current_etag_fails(self) -> None:
        request = fastapi.Request(
            scope={
                'method': 'PUT',
                'type': 'http',
                'headers': [(b'if-match', b'"etag"')],
            }
        )

        with self.assertRaises(fastapi.HTTPException) as ctx:
            conditional.evaluate_conditional_request(request)
        self.assertEqual(
            http.HTTPStatus.PRECONDITION_FAILED,
            ctx.exception.status_code,
        )

    def test_if_none_match_uses_weak_comparison(self) -> None:
        etag = secrets.token_hex(16)
        for variant in (f'"{etag}"', f'W/"{etag}"', '*'):
            request = fastapi.Request(
                scope={
                    'method': 'GET',
                    'type': 'http',
                    'headers': [(b'if-none-match', variant.encode('ascii'))],
                }
            )
            with self.assertRaises(fastapi.HTTPException) as ctx:
                conditional.evaluate_conditional_request(
                    request,
                    etag=etag,
                )
            self.assertEqual(
                http.HTTPStatus.NOT_MODIFIED,
                ctx.exception.status_code,
                f'Variant {variant!r}',
            )
            headers = fastapi_utilities.unwrap(ctx.exception.headers)
            self.assertEqual(headers['etag'], f'"{etag}"')
            self.assertNotIn('last-modified', headers)

    def test_if_none_match_precedes_if_modified_since(self) -> None:
        now = dates.utcnow()
        etag = secrets.token_hex(16)
        request = fastapi.Request(
            scope={
                'method': 'GET',
                'type': 'http',
                'headers': [
                    (b'if-none-match', b'"different"'),
                    (
                        b'if-modified-since',
                        dates.http_date(now).encode('ascii'),
                    ),
                ],
            }
        )

        conditional.evaluate_conditional_request(
            request,
            etag=etag,
            last_modified=now,
        )

    def test_if_none_match_with_post(self) -> None:
        etag = secrets.token_hex(16)
        request = fastapi.Request(
            scope={
                'method': 'POST',
                'type': 'http',
                'headers': [(b'if-none-match', f'"{etag}"'.encode('ascii'))],
            }
        )
        with self.assertRaises(fastapi.HTTPException) as ctx:
            conditional.evaluate_conditional_request(request, etag=etag)
        self.assertEqual(
            http.HTTPStatus.PRECONDITION_FAILED,
            ctx.exception.status_code,
        )
        headers = fastapi_utilities.unwrap(ctx.exception.headers)
        self.assertEqual(headers['etag'], f'"{etag}"')
        self.assertNotIn('last-modified', headers)

    def test_if_none_match_star_does_not_require_etag(self) -> None:
        request = fastapi.Request(
            scope={
                'method': 'GET',
                'type': 'http',
                'headers': [(b'if-none-match', b'*')],
            }
        )
        with self.assertRaises(fastapi.HTTPException) as ctx:
            conditional.evaluate_conditional_request(request)
        self.assertEqual(
            http.HTTPStatus.NOT_MODIFIED,
            ctx.exception.status_code,
        )

    def test_ignores_preconditions_for_options(self) -> None:
        request = fastapi.Request(
            scope={
                'method': 'OPTIONS',
                'type': 'http',
                'headers': [(b'if-none-match', b'*')],
            }
        )

        conditional.evaluate_conditional_request(request)

    def test_invalid_if_none_match_value_is_ignored(self) -> None:
        request = fastapi.Request(
            scope={
                'method': 'GET',
                'type': 'http',
                'headers': [(b'if-none-match', b'not-an-etag')],
            }
        )

        conditional.evaluate_conditional_request(request, etag='etag')

    def test_parse_entity_tag_list_ignores_invalid_members(self) -> None:
        tags = list(
            conditional._parse_entity_tag_list('W/"one", invalid, "two",')
        )
        self.assertEqual(
            [
                conditional._EntityTag(weak=True, opaque_tag='one'),
                conditional._EntityTag(weak=False, opaque_tag='two'),
            ],
            tags,
        )

    def test_normalize_optional_http_date_returns_none_for_invalid_string(
        self,
    ) -> None:
        self.assertIsNone(
            conditional._normalize_optional_http_date('not-a-date')
        )

    def test_normalize_http_date_raises_type_error_for_unexpected_type(
        self,
    ) -> None:
        with self.assertRaises(TypeError):
            conditional._normalize_http_date(
                t.cast('conditional.HTTPDateInput', object())
            )


class Entity(pydantic.BaseModel):
    digest: str | None
    last_modified: datetime.datetime | float | str | None


IMF_DATE = datetime.datetime(1994, 11, 6, 8, 49, 37, tzinfo=datetime.UTC)


async def get_entity() -> Entity:
    return Entity(last_modified=IMF_DATE, digest='digest')


CacheableEntity = t.Annotated[
    Entity,
    fastapi.Depends(conditional.conditional_dependency(get_entity)),
]


class ConditionalDependencyTests(unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.handler_invoked = False
        self.app = fastapi.FastAPI()
        self.app.add_api_route('/test', self.handler, methods=['GET'])
        self.client = fastapi.testclient.TestClient(self.app)

    async def handler(self, entity: CacheableEntity) -> Entity:
        self.handler_invoked = True
        return entity

    def test_without_conditional_headers(self) -> None:
        client = fastapi.testclient.TestClient(self.app)
        response = client.get('/test')
        self.assertTrue(self.handler_invoked)
        self.assertEqual(http.HTTPStatus.OK, response.status_code)

    def test_if_match_star(self) -> None:
        response = self.client.get('/test', headers={'if-match': '*'})
        self.assertTrue(self.handler_invoked)
        self.assertEqual(http.HTTPStatus.OK, response.status_code)

    def test_if_match_etag(self) -> None:
        response = self.client.get('/test', headers={'if-match': '"digest"'})
        self.assertTrue(self.handler_invoked)
        self.assertEqual(http.HTTPStatus.OK, response.status_code)

    def test_if_match_etag_list(self) -> None:
        response = self.client.get(
            '/test', headers={'if-match': '"digest", "other"'}
        )
        self.assertTrue(self.handler_invoked)
        self.assertEqual(http.HTTPStatus.OK, response.status_code)

    def test_if_match_etag_mismatch(self) -> None:
        response = self.client.get('/test', headers={'if-match': '"other"'})
        self.assertFalse(self.handler_invoked)
        self.assertEqual(
            http.HTTPStatus.PRECONDITION_FAILED, response.status_code
        )

    def test_with_last_modified_since(self) -> None:
        client = fastapi.testclient.TestClient(self.app)

        then = IMF_DATE.strftime('%a, %d %b %Y %H:%M:%S GMT')
        response = client.get('/test', headers={'if-modified-since': then})
        self.assertFalse(self.handler_invoked)
        self.assertEqual(304, response.status_code)

        then = (IMF_DATE + datetime.timedelta(days=1)).strftime(
            '%a, %d %b %Y %H:%M:%S GMT'
        )
        response = client.get('/test', headers={'if-modified-since': then})
        self.assertFalse(self.handler_invoked)
        self.assertEqual(304, response.status_code)


class SettingsTestSettings(pydantic_settings.BaseSettings):
    model_config = {'env_prefix': 'TEST_'}
    value: int


class SettingsFromEnvironmentTests(unittest.TestCase):
    @contextlib.contextmanager
    def override_environment(
        self, **kwargs: str | None
    ) -> abc.Generator[None]:
        modifications: dict[str, str | None] = {}
        for key, value in kwargs.items():
            modifications.setdefault(key, os.environ.pop(key, None))
            if value is not None:
                os.environ[key] = value
        try:
            yield
        finally:
            for key, value in modifications.items():
                os.environ.pop(key, None)
                if value is not None:
                    os.environ[key] = value

    def test_with_env_defined(self) -> None:
        with self.override_environment(TEST_VALUE='1234'):
            cfg = settings.from_environment(SettingsTestSettings)
            self.assertEqual(1234, cfg.value)

    def test_with_missing_env(self) -> None:
        with (
            self.override_environment(TEST_VALUE=None),
            self.assertRaises(RuntimeError) as cm,
        ):
            settings.from_environment(SettingsTestSettings)
        self.assertIn('TEST_VALUE', str(cm.exception), str(cm.exception))

    def test_with_invalid_env_value(self) -> None:
        with (
            self.override_environment(TEST_VALUE='not-a-number'),
            self.assertRaises(RuntimeError) as cm,
        ):
            settings.from_environment(SettingsTestSettings)
        self.assertTrue(
            str(cm.exception).lower().startswith('invalid settings'),
            str(cm.exception),
        )
