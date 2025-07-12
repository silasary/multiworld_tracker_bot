import pymongo

from shared import configuration

configuration.DEFAULTS["mongo_uri"] = "mongodb://localhost:27017/"

client = pymongo.MongoClient(configuration.get("mongo_uri"))

db = client["multiworld_tracker"]
