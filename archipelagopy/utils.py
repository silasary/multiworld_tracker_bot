import json
import logging
from functools import lru_cache
import os
import typing


def cache_path(*path: str) -> str:
    """Returns path to a file in the user's Archipelago cache directory."""
    if hasattr(cache_path, "cached_path"):
        pass
    else:
        import platformdirs

        cache_path.cached_path = platformdirs.user_cache_dir("Archipelago", False)

    return os.path.join(cache_path.cached_path, *path)


def get_file_safe_name(name: str) -> str:
    return "".join(c for c in name if c not in '<>:"/\\|?*')


@lru_cache(maxsize=128)
def load_data_package_for_checksum(game: str, checksum: typing.Optional[str]) -> dict[str, typing.Any]:
    if checksum and game:
        if checksum != get_file_safe_name(checksum):
            raise ValueError(f"Bad symbols in checksum: {checksum}")
        path = cache_path("datapackage", get_file_safe_name(game), f"{checksum}.json")
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8-sig") as f:
                    return json.load(f)
            except Exception as e:
                logging.debug(f"Could not load data package: {e}")

    # cache does not match
    return {}


def store_data_package_for_checksum(game: str, data: typing.Dict[str, typing.Any]) -> None:
    checksum = data.get("checksum")
    if checksum and game:
        if checksum != get_file_safe_name(checksum):
            raise ValueError(f"Bad symbols in checksum: {checksum}")
        game_folder = cache_path("datapackage", get_file_safe_name(game))
        os.makedirs(game_folder, exist_ok=True)
        try:
            with open(os.path.join(game_folder, f"{checksum}.json"), "w", encoding="utf-8-sig") as f:
                json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
        except Exception as e:
            logging.debug(f"Could not store data package: {e}")


async def fetch_datapackage_from_webhost(game: str, checksum: str) -> dict[str, typing.Any]:
    """Fetch a datapackage from the Archipelago webhost."""
    import aiohttp

    data = load_data_package_for_checksum(game, checksum)
    if data:
        return data

    url = f"https://archipelago.gg/api/datapackage/{checksum}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            if response.status != 200:
                raise ValueError(f"Could not fetch datapackage from {url}, status code {response.status}")
            data = await response.json()
            store_data_package_for_checksum(game, data)
            return data
