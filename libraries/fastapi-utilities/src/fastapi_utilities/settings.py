import pydantic
import pydantic_settings


def from_environment[S: pydantic_settings.BaseSettings](
    cls: type[S],
) -> S:
    """Load a pydantic settings class from the environment.

    Raises:
        RuntimeError: If the environment variables are missing or invalid.
    """
    try:
        return cls()
    except pydantic.ValidationError as e:
        prefix = cls.model_config.get('env_prefix', '')
        missing_vars: set[str] = set()
        for error in e.errors():
            if error['type'] == 'missing':
                missing_vars.add(f'{prefix}{error["loc"][0]}'.upper())
        if missing_vars:
            raise RuntimeError(
                'Missing environment variables: ' + ', '.join(missing_vars)
            ) from e
        raise RuntimeError(
            f'Invalid settings for {cls.__name__}: '
            + ', '.join(e['msg'] for e in e.errors())
        ) from e
