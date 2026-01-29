from ap_alert.models.cheese_game import CheeseGame
from ap_alert.models.enums import HintClassification, HintUpdate, TrackerStatus


import attrs


@attrs.define()
class Hint:
    id: str
    item: str
    location: str
    entrance: str
    found: bool
    classification: HintClassification
    finder_game_id: int
    receiver_game_id: int | None = attrs.field(default=None)
    item_link_name: str | None = None

    update: HintUpdate = attrs.field(default=HintUpdate.none, init=False)
    is_finder: bool = attrs.field(default=False)

    @property
    def useless(self) -> bool:
        from ap_alert.multiworld import GAMES

        game = GAMES.get(self.receiver_game_id)
        return game and game.tracker_status == TrackerStatus.goal_completed

    def embed(self) -> dict:
        from ap_alert.multiworld import DATAPACKAGES, GAMES

        if self.update == HintUpdate.new:
            title = "New Hint"
        elif self.update == HintUpdate.found:
            title = "Hint Found"
        elif self.update == HintUpdate.classified:
            title = "Hint Reclassified"
        elif self.update == HintUpdate.useless:
            title = "Hint no longer needed"
        else:
            title = "Hint"
        receiver = GAMES.get(self.receiver_game_id)
        finder = GAMES.get(self.finder_game_id)

        if receiver is None:
            receiver = CheeseGame({"name": self.item_link_name or "(Item Link)"})
        if finder is None:
            finder = CheeseGame()

        item = f"{DATAPACKAGES[receiver.game].icon(self.item)} {self.item}"
        if self.is_finder:
            description = f"***{receiver.name}***'s ***{item}*** is at ***{self.location}***"
        else:
            description = f"***{receiver.name}***'s ***{item}*** is at {finder.name}'s ***{self.location}***"

        if self.entrance and self.entrance != "Vanilla":
            description += f" ({self.entrance})"

        # if self.classification == HintClassification.critical:
        #     description = f"❗ {description}"
        # elif self.classification == HintClassification.trash:
        #     description = f"🗑️ {description}"
        # elif self.classification == HintClassification.useful:
        #     description = f"🙋 {description}"

        embed = {
            "title": title,
            "description": description,
            # "color": self.classification.color,
            # "footer": {"text": f"Hint ID: {self.id}"},
        }
        if self.classification != HintClassification.unknown:
            embed["fields"] = [{"name": "Classification", "value": self.classification.title()}]

        return embed
