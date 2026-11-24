"""Post to a test client once and close it."""

from aiohttp import web
from aiohttp.test_utils import TestClient


async def post_once(
    *,
    client: TestClient[web.Request, web.Application],
    path: str,
    data: dict[str, str],
) -> int:
    """
    Post a form, then close the client.

    Parameters
    ----------
    client : TestClient
        A client of a started server.
    path : str
        Where to post.
    data : dict[str, str]
        The form fields.

    Returns
    -------
    int
        The response status.
    """
    try:
        response = await client.post(path, data=data)
    finally:
        await client.close()
    return response.status
