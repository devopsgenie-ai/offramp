import os

from fastapi import APIRouter

from services.db import get_collection

router = APIRouter(prefix="/items")

PAGE_SIZE = int(os.environ.get("ITEMS_PAGE_SIZE", "20"))


@router.get("")
async def list_items():
    collection = get_collection()
    return await collection.find().to_list(PAGE_SIZE)
