import os

from motor.motor_asyncio import AsyncIOMotorClient

MONGO_URL = os.environ["MONGO_URL"]
DB_NAME = os.environ.get("DB_NAME", "widgets")

_client = AsyncIOMotorClient(MONGO_URL)


def get_collection():
    return _client[DB_NAME]["items"]
