"""The ways fetching or reading a realtime feed fails."""

from http import HTTPStatus


class FeedError(Exception):
    """
    A feed couldn't be fetched.

    Parameters
    ----------
    message : str
        What failed.
    detail : str
        Why the server refused the request, when it said; empty otherwise.
    """

    def __init__(self, message: str, *, detail: str = '') -> None:
        super().__init__(message)
        self.detail = detail


class FeedUnavailable(FeedError):
    """The feed server timed out or answered with a server error (5xx)."""


class FeedTimedOut(FeedUnavailable):
    """The feed server took too long to answer."""


class FeedThrottled(FeedError):
    """
    The feed server refused a feed for going over its rate limit (429).

    Parameters
    ----------
    feed : str
        Feed path.
    retry_after_s : float | None
        Seconds the server asked to wait, or None when it didn't say.
    detail : str
        Why the server refused the request, when it said; empty otherwise.
    """

    def __init__(
        self, *, feed: str, retry_after_s: float | None, detail: str = '',
    ) -> None:
        super().__init__(
            f'{feed}: HTTP {HTTPStatus.TOO_MANY_REQUESTS}', detail=detail,
        )
        self.retry_after_s = retry_after_s
