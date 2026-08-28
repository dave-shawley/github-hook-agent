import annotationlib
import contextlib
import http
import inspect
import types
import typing as t
from collections import abc

import fastapi
import fastapi_utilities

ConfigHook = abc.Callable[[fastapi.FastAPI], None]
LifespanState = dict[str, object]


class _CMReturning[T](t.Protocol):
    async def __aenter__(self) -> T: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        exc_traceback: types.TracebackType | None,
        /,
    ) -> bool | None: ...


type LifespanHook = (
    abc.Callable[[fastapi.FastAPI], _CMReturning[object | None]]
    | abc.Callable[[], _CMReturning[object | None]]
)
type TypedLifespanHook[T] = (
    abc.Callable[[fastapi.FastAPI], _CMReturning[T]]
    | abc.Callable[[], _CMReturning[T]]
)


class Lifespan(dict[LifespanHook, object | None]):
    def __init__(self) -> None:
        super().__init__()
        self._called = False
        self._lifespans: list[LifespanHook] = []

    def add_lifespan(self, hook: LifespanHook) -> None:
        if self._called:
            raise RuntimeError(
                'Cannot add lifespan after __call__ has been invoked'
            )
        self._lifespans.append(hook)

    def get_state[T](self, hook: TypedLifespanHook[T]) -> T:
        try:
            return t.cast('T', self[hook])
        except KeyError:
            raise fastapi.HTTPException(
                status_code=http.HTTPStatus.INTERNAL_SERVER_ERROR,
                detail=f'Unmet lifespan dependency: {hook!r}',
            ) from None

    def __call__(
        self, app: fastapi.FastAPI
    ) -> contextlib.AbstractAsyncContextManager[LifespanState]:
        self._called = True

        @contextlib.asynccontextmanager
        async def cm() -> abc.AsyncGenerator[dict[str, object]]:
            async with contextlib.AsyncExitStack() as stack:
                for hook in self._lifespans:
                    if hook not in self:
                        self[hook] = await stack.enter_async_context(
                            _invoke_hook(hook, app)
                        )
                yield {'lifespan_data': self}

        return cm()


def _get_lifespan(request: fastapi.Request) -> Lifespan:
    try:
        lifespan = request.state.lifespan_data
    except AttributeError, ValueError:
        logger = fastapi_utilities.get_logger(_get_lifespan)
        logger.exception('Lifespan is not available in %r', set(request.state))
        raise fastapi.HTTPException(
            status_code=http.HTTPStatus.INTERNAL_SERVER_ERROR,
            detail='Lifespan not available',
        ) from None

    if not isinstance(lifespan, Lifespan):
        raise fastapi.HTTPException(
            status_code=http.HTTPStatus.INTERNAL_SERVER_ERROR,
            detail=f'Unexpected lifespan type: {type(lifespan)}',
        )

    return lifespan


type LifespanMap = t.Annotated[Lifespan, fastapi.Depends(_get_lifespan)]


class _HookWithoutApp[T](t.Protocol):
    def __call__(self) -> _CMReturning[T]: ...


class _HookWithApp[T](t.Protocol):
    def __call__(self, app: fastapi.FastAPI, /) -> _CMReturning[T]: ...


def _invoke_hook[T](
    hook: _HookWithoutApp[T] | _HookWithApp[T],
    app: fastapi.FastAPI,
) -> _CMReturning[T]:
    params = tuple(
        inspect.signature(
            hook,
            annotation_format=annotationlib.Format.STRING,
        ).parameters.values()
    )
    if not params:
        return t.cast('_HookWithoutApp[T]', hook)()
    if len(params) == 1 and params[0].kind in (
        inspect.Parameter.POSITIONAL_ONLY,
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
    ):
        return t.cast('_HookWithApp[T]', hook)(app)
    raise TypeError(f'Invalid lifespan hook signature: {hook!r}')
