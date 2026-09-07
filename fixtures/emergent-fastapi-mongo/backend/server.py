import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List

from dotenv import load_dotenv
from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, Field

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

mongo_url = os.environ["MONGO_URL"]
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ["DB_NAME"]]

app = FastAPI(title="Status Board")

# Every route in an Emergent backend is mounted under /api. The platform ingress
# routes /api to this service and everything else to the static frontend, so the
# prefix is load-bearing and must not be changed.
api_router = APIRouter(prefix="/api")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class StatusCheck(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    client_name: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class StatusCheckCreate(BaseModel):
    client_name: str


@api_router.get("/health")
async def health():
    return {"status": "ok"}


@api_router.get("/")
async def root():
    return {"message": "Hello World"}


@api_router.post("/status", response_model=StatusCheck)
async def create_status_check(payload: StatusCheckCreate):
    check = StatusCheck(client_name=payload.client_name)
    await db.status_checks.insert_one(check.model_dump())
    return check


@api_router.get("/status", response_model=List[StatusCheck])
async def get_status_checks():
    documents = await db.status_checks.find().to_list(1000)
    return [StatusCheck(**document) for document in documents]


@api_router.delete("/status/{check_id}")
async def delete_status_check(check_id: str):
    result = await db.status_checks.delete_one({"id": check_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="not found")
    return {"deleted": check_id}


app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8001)
