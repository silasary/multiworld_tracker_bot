import inspect
import json
import os
from typing import Any

from .exceptions import InvalidArgumentException

DEFAULTS = {
    "token": "",
    "owners": [154363842451734528, 222352614832996354],
    "sentry_dsn": "https://7aadf0c15f880e90e01c4dba496f152d@o233010.ingest.us.sentry.io/4507219660832768",
    }


def get(key: str) -> Any:
    try:
        cfg = json.load(open("config.json"))
    except FileNotFoundError:
        cfg = {}
    if key in cfg:
        return cfg[key]
    elif key in os.environ:
        cfg[key] = os.environ[key]
    elif key in DEFAULTS:
        # Lock in the default value if we use it.
        cfg[key] = DEFAULTS[key]

        if inspect.isfunction(cfg[key]):  # If default value is a function, call it.
            cfg[key] = cfg[key]()
    else:
        raise InvalidArgumentException("No default or other configuration value available for {key}".format(key=key))

    print("CONFIG: {0}={1}".format(key, cfg[key]))
    fh = open("config.json", "w")
    fh.write(json.dumps(cfg, indent=4))
    return cfg[key]


def write(key: str, value: str) -> str:
    try:
        cfg = json.load(open("config.json"))
    except FileNotFoundError:
        cfg = {}

    cfg[key] = value

    print("CONFIG: {0}={1}".format(key, cfg[key]))
    fh = open("config.json", "w")
    fh.write(json.dumps(cfg, indent=4, sort_keys=True))
    return cfg[key]
