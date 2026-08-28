import contextlib
import datetime
import http
import re
import typing as t
from collections import abc

import fastapi

from fastapi_utilities import dates


class CacheableEntity(t.Protocol):
    digest: str | None
    last_modified: datetime.datetime | float | str | None


type HTTPDateInput = datetime.datetime | int | float | str


type CacheableProvider[T: CacheableEntity] = t.Callable[
    ..., T | abc.Awaitable[T]
]


# This is defined HERE since ruff check does not recognize NoReturn
# unless the function is defined BEFORE its usage.
def _raise_unexpected_http_date(value: t.Never) -> t.NoReturn:
    raise TypeError(f'Unexpected type for last_modified: {type(value)}')


def evaluate_conditional_request(
    request: fastapi.Request,
    *,
    last_modified: datetime.datetime | float | str | None = None,
    etag: str | None = None,
) -> None:
    """Evaluate conditional request headers against content properties.

    This function implements Conditional Request logic as described in
    section 13 of RFC-9110 based on the request headers and supplied
    content properties. If the request is conditional and the condition
    is not met, a fastapi.HTTPException is raised with the appropriate
    status code (304 or 412).

    Note:
        RFC-9110 permits a 2xx response to `if-match` and
        `if-unmodified-since` when:

        > if the request is a state-changing operation that
        > appears to have already been applied to the
        > selected representation

        This function does not support that scenario. It will ALWAYS
        raise a `PreconditionFailed` exception.

    Raises:
        fastapi.HTTPException: If the request is conditional and the
          condition is not met.

    """
    if request.method in ('CONNECT', 'OPTIONS', 'TRACE'):
        return

    headers = generate_entity_headers(etag=etag, last_modified=last_modified)
    last_modified_date = _normalize_optional_http_date(last_modified)
    current_etag = (
        _EntityTag(weak=False, opaque_tag=etag) if etag is not None else None
    )

    # RFC-9110, Section 13.1.1
    # An origin server MUST use the strong comparison function
    # when comparing entity tags for If-Match
    # An origin server that evaluates an If-Match condition MUST
    # NOT perform the requested method if the condition evaluates
    # to false. Instead, the origin server MAY indicate that the
    # conditional request failed by responding with a 412
    # (Precondition Failed) status code...
    # A client MAY send an If-Match header field in a GET request
    # to indicate that it would prefer a 412 (Precondition Failed)
    # response if the selected representation does not match
    if_match = _get_single_header_value(request, 'if-match')
    if if_match is not None and not _etag_list_matches(
        if_match, current_etag, weak=False, star_matches=True
    ):
        _raise_conditional_failure(
            http.HTTPStatus.PRECONDITION_FAILED, headers
        )

    # RFC-9110, Section 13.1.4
    # A recipient MUST ignore If-Unmodified-Since if the request
    # contains an If-Match header field...
    # A recipient MUST ignore the If-Unmodified-Since header field
    # if the received field value is not a valid HTTP-date (including
    # when the field value appears to be a list of dates).
    # An origin server that evaluates an If-Unmodified-Since condition
    # MUST NOT perform the requested method if the condition evaluates
    # to false. Instead, the origin server MAY indicate that the
    # conditional request failed by responding with a 412 (Precondition
    # Failed) status code...
    # A client MAY send an If-Unmodified-Since header field in a GET
    # request to indicate that it would prefer a 412 (Precondition Failed)
    # response if the selected representation has been modified.
    if if_match is None:
        if_unmodified_since = _parse_single_http_date_header(
            request, 'if-unmodified-since'
        )
        if (
            if_unmodified_since is not None
            and last_modified_date is not None
            and last_modified_date > if_unmodified_since
        ):
            _raise_conditional_failure(
                http.HTTPStatus.PRECONDITION_FAILED, headers
            )

    # RFC-9110, Section 13.1.2
    # A recipient MUST use the weak comparison function when
    # comparing entity tags for If-None-Match.
    # An origin server that evaluates an If-None-Match condition
    # MUST NOT perform the requested method if the condition
    # evaluates to false; instead, the origin server MUST respond
    # with either a) the 304 (Not Modified) status code if the
    # request method is GET or HEAD or b) the 412 (Precondition
    # Failed) status code for all other request methods.
    if_none_match = _get_single_header_value(request, 'if-none-match')
    if if_none_match is not None and _etag_list_matches(
        if_none_match, current_etag, weak=True, star_matches=True
    ):
        _raise_conditional_failure(
            http.HTTPStatus.NOT_MODIFIED
            if request.method in ('GET', 'HEAD')
            else http.HTTPStatus.PRECONDITION_FAILED,
            headers,
        )

    # RFC-9110, Section 13.1.3
    # A recipient MUST ignore If-Modified-Since if the request
    # contains an If-None-Match header field; ...
    # A recipient MUST ignore the If-Modified-Since header field
    # if the received field value is not a valid HTTP-date, the
    # field value has more than one member, or if the request
    # method is neither GET nor HEAD.
    if (
        request.method in ('GET', 'HEAD')
        and if_none_match is None
        and (
            if_modified_since := _parse_single_http_date_header(
                request, 'if-modified-since'
            )
        )
        is not None
        and last_modified_date is not None
        and last_modified_date <= if_modified_since
    ):
        _raise_conditional_failure(http.HTTPStatus.NOT_MODIFIED, headers)


def conditional_dependency[T: CacheableEntity](
    dependency_provider: CacheableProvider[T],
) -> CacheableProvider[T]:
    """Return a dependency that evaluates conditional requests.

    This is a parameterizable dependency factory that implements
    conditional request evaluation based on an entity returned
    by a dependency provider.

    Example:

        class Entity(pydantic.BaseModel):
            digest: str | None
            last_modified: datetime.datetime | float | str | None
            other_fields: object

        async def retrieve_entity(request: fastapi.Request) -> Entity:
            ...

        CacheableEntity = t.Annotated[
            Entity,
            fastapi.Depends(conditional_dependency(retrieve_entity))
        ]

        @app.get('/')
        async def get_entity(*, entity: CacheableEntity) -> Entity:
            ...

    """
    dependency_injector = fastapi.Depends(dependency_provider)

    async def dependency(
        request: fastapi.Request,
        response: fastapi.Response,
        cached: T = dependency_injector,
    ) -> T:
        response.headers.update(
            generate_entity_headers(
                etag=cached.digest, last_modified=cached.last_modified
            )
        )
        evaluate_conditional_request(
            request,
            last_modified=cached.last_modified,
            etag=cached.digest,
        )
        return cached

    return dependency


def generate_entity_headers(
    *,
    etag: str | None = None,
    last_modified: HTTPDateInput | None = None,
) -> abc.Mapping[str, str]:
    """Generate entity-related headers based on parameters."""
    entity_headers: dict[str, str] = {}
    if etag:
        entity_headers['etag'] = f'"{etag}"'
    if last_modified is not None:
        entity_headers['last-modified'] = (
            last_modified
            if isinstance(last_modified, str)
            else dates.http_date(last_modified)
        )
    return entity_headers


def _normalize_http_date(value: HTTPDateInput) -> datetime.datetime:
    """Convert a date value to a datetime object.

    The conversion includes truncating sub-second values and
    moving the value into UTC. This coerces values into the form
    described in RFC-9100.

    https://www.rfc-editor.org/rfc/rfc9110.html#name-date-time-formats

    Raises:
        ValueError: If the input is not a valid date value.
    """
    match value:
        case datetime.datetime():
            return value.astimezone(datetime.UTC).replace(microsecond=0)
        case int() | float():
            return datetime.datetime.fromtimestamp(
                value, datetime.UTC
            ).replace(microsecond=0)
        case str():
            return dates.parse_http_date(value)
        case _:
            _raise_unexpected_http_date(value)


def _normalize_optional_http_date(
    value: HTTPDateInput | None,
) -> datetime.datetime | None:
    """Normalize an optional HTTP date to a datetime object.

    Returns:
        The normalized datetime object, or None if the input was None.
    """
    if value is None:
        return None

    with contextlib.suppress(ValueError):
        return _normalize_http_date(value)

    return None


def _raise_conditional_failure(
    code: http.HTTPStatus,
    headers: abc.Mapping[str, str],
) -> t.NoReturn:
    raise fastapi.HTTPException(code, headers=headers)


def _get_single_header_value(
    request: fastapi.Request, header_name: str
) -> str | None:
    """Retrieve the `header_name` value is a single value.

    Returns:
        The single value, or None if the header is not present or
        is present more than once.
    """
    values = request.headers.getlist(header_name)
    if len(values) != 1:
        return None
    return values[0]


def _parse_single_http_date_header(
    request: fastapi.Request, header_name: str
) -> datetime.datetime | None:
    """Retrieve the `header_name` value as a single HTTP date.

    Returns:
        The parsed HTTP date, or None if the header is not present,
        is present more than once, or is an invalid date.
    """
    value = _get_single_header_value(request, header_name)
    if value is None:
        return None

    with contextlib.suppress(ValueError):
        return dates.parse_http_date(value)

    return None


class _EntityTag(t.NamedTuple):
    weak: bool
    opaque_tag: str


_ENTITY_TAG_RE = re.compile(r'^(W/)?"([^"]*)"$')


def _etag_list_matches(
    value: str,
    current: _EntityTag | None,
    *,
    weak: bool,
    star_matches: bool,
) -> bool:
    if value == '*':
        return star_matches
    if current is None:
        return False

    compare = _weak_etag_matches if weak else _strong_etag_matches
    return any(
        compare(candidate, current)
        for candidate in _parse_entity_tag_list(value)
    )


def _parse_entity_tag_list(value: str) -> abc.Iterator[_EntityTag]:
    for candidate in _split_commas_outside_quotes(value):
        match = _ENTITY_TAG_RE.fullmatch(candidate)
        if match is not None:
            yield _EntityTag(
                weak=match.group(1) is not None,
                opaque_tag=match.group(2),
            )


def _split_commas_outside_quotes(value: str) -> abc.Iterator[str]:
    in_quotes = False
    current: list[str] = []
    for char in value:
        if char == '"':
            in_quotes = not in_quotes
            current.append(char)
        elif char == ',' and not in_quotes:
            yield ''.join(current).strip()
            current = []
        else:
            current.append(char)

    trailing = ''.join(current).strip()
    if trailing:
        yield trailing


def _strong_etag_matches(left: _EntityTag, right: _EntityTag) -> bool:
    return (
        not left.weak
        and not right.weak
        and left.opaque_tag == right.opaque_tag
    )


def _weak_etag_matches(left: _EntityTag, right: _EntityTag) -> bool:
    return left.opaque_tag == right.opaque_tag
