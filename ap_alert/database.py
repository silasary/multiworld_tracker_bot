import attrs
import pymongo
from bson import ObjectId
from interactions.client.smart_cache import TTLCache

from ap_alert.models.player import Player
from shared import configuration
from .models.tracked_game import TrackedGame
from shared.automongocache import from_dict, to_dict

configuration.DEFAULTS["mongo_uri"] = "mongodb://localhost:27017/"
configuration.DEFAULTS["mongo_collection"] = "multiworld_tracker"

client = pymongo.AsyncMongoClient(configuration.get("mongo_uri"))
db = client[configuration.get("mongo_collection")]

tracker_collection = db["trackers"]
player_collection = db["players"]


@attrs.define(eq=False, order=False, hash=False, kw_only=False)
class Database:
    tracker_cache: TTLCache = attrs.field(repr=False, factory=TTLCache)  # key: (object_id)
    player_cache: TTLCache = attrs.field(repr=False, factory=TTLCache)  # key: (player_id)

    async def fetch_tracker(self, object_id: str) -> TrackedGame | None:
        if isinstance(object_id, ObjectId):
            object_id = str(object_id)
        if object_id in self.tracker_cache:
            return self.tracker_cache[object_id]

        document = await tracker_collection.find_one({"_id": ObjectId(object_id)})
        if document is None:
            return None

        return self.place_tracker(document)

    def place_tracker(self, document: dict) -> TrackedGame:
        object_id = str(document["_id"])
        tracker = from_dict(document, TrackedGame)
        self.tracker_cache[object_id] = tracker
        return tracker

    async def save_tracker(self, tracker: TrackedGame) -> None:
        data = to_dict(tracker)
        del data["_id"]
        if tracker._id is None or tracker._id == "None":
            result = await tracker_collection.insert_one(data)
            tracker._id = str(result.inserted_id)
            self.tracker_cache[tracker._id] = tracker
        else:
            await tracker_collection.update_one(
                {"_id": ObjectId(tracker._id)},
                {"$set": data},
                upsert=True,
            )

    async def set_cheese_id(self, tracker: TrackedGame, cheese_id: int):
        tracker.cheese_id = cheese_id
        await tracker_collection.update_one(
            {"_id": ObjectId(tracker._id)},
            {"$set": {"cheese_id": cheese_id}},
            upsert=True,
        )

    async def fetch_player(self, player_id: int) -> Player | None:
        if player_id in self.player_cache:
            return self.player_cache[player_id]
        document = await player_collection.find_one({"id": player_id})
        player = from_dict(document, Player) if document else None
        if player:
            self.player_cache[player_id] = player
        return player

    async def save_player(self, player: Player) -> None:
        await player_collection.update_one(
            {"id": player.id},
            {"$set": to_dict(player)},
            upsert=True,
        )

    async def fetch_all_players(self) -> list[Player]:
        players = []
        async for document in player_collection.find({}):
            player = from_dict(document, Player)
            self.player_cache[player.id] = player
            players.append(player)
        return players


DATABASE = Database()
