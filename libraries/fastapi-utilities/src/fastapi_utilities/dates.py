import datetime


def utcnow() -> datetime.datetime:
    """Return the current UTC datetime."""
    return datetime.datetime.now(datetime.UTC)


def http_date(timestamp: datetime.datetime | float) -> str:
    """Return an HTTP date string for a given timestamp."""
    if isinstance(timestamp, float):
        timestamp = datetime.datetime.fromtimestamp(timestamp, datetime.UTC)
    if not isinstance(timestamp, datetime.datetime):
        raise TypeError(f'Expected datetime.datetime, got {type(timestamp)}.')
    return (
        timestamp.astimezone(datetime.UTC)
        .replace(microsecond=0)
        .strftime('%a, %d %b %Y %H:%M:%S GMT')
    )


def parse_http_date(date_str: str) -> datetime.datetime:
    """Parse an HTTP date string into a datetime object."""
    try:
        return datetime.datetime.strptime(
            date_str, '%a, %d %b %Y %H:%M:%S GMT'
        ).replace(tzinfo=datetime.UTC)
    except Exception as e:
        raise ValueError(f'Failed to parse {date_str!r} as HTTP date.') from e
