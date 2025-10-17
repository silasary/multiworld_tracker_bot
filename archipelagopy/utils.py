import os


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
