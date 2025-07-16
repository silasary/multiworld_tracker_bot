import datetime
import cattrs

from ap_alert.multiworld import CheeselessMultiworld, Multiworld


converter = cattrs.Converter()

converter.register_structure_hook(datetime.datetime, lambda x, *_: datetime.datetime.fromisoformat(x) if x else None)
converter.register_unstructure_hook(datetime.datetime, lambda x, *_: x.isoformat() if x else None)


def structure_multiworld(x, *_):
    if x["url"].startswith("https://cheesetrackers.theincrediblewheelofchee.se/api/tracker/"):
        return converter.structure(x, Multiworld)

    return converter.structure(x, CheeselessMultiworld)


converter.register_structure_hook(Multiworld | CheeselessMultiworld, structure_multiworld)
