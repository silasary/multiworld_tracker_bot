import aiohttp


def make_session() -> aiohttp.ClientSession:
    """Create an aiohttp ClientSession with default headers."""
    headers = {"User-Agent": "MultiworldTrackerBot/Silasary"}
    return aiohttp.ClientSession(headers=headers)
