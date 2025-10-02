import datetime
import cattrs

from ap_alert.multiworld import Multiworld


converter = cattrs.Converter()

converter.register_structure_hook(datetime.datetime, lambda x, *_: datetime.datetime.fromisoformat(x) if x else None)
converter.register_unstructure_hook(datetime.datetime, lambda x, *_: x.isoformat() if x else None)


def structure_multiworld(x, *_):
    if x["url"].startswith("https://cheesetrackers.theincrediblewheelofchee.se/api/tracker/"):
        return converter.structure(x, Multiworld)

    return converter.structure(x, Multiworld)


converter.register_structure_hook(Multiworld, structure_multiworld)
