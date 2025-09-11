import pymongo

from shared import configuration

configuration.DEFAULTS["mongo_uri"] = "mongodb://localhost:27017/"
configuration.DEFAULTS["mongo_collection"] = "multiworld_tracker"

client = pymongo.MongoClient(configuration.get("mongo_uri"))

db = client[configuration.get("mongo_collection")]
