from world_data.models import ItemClassification


import attrs


@attrs.define()
class NetworkItem:
    name: str
    game: str
    quantity: int
    flags: ItemClassification = ItemClassification.unknown

    @property
    def classification(self) -> ItemClassification:
        from ap_alert.multiworld import DATAPACKAGES

        if self.flags != ItemClassification.unknown:
            return self.flags
        return DATAPACKAGES[self.game].items.get(self.name, ItemClassification.unknown)
