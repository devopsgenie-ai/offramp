from fastapi import FastAPI

from routes.health import router as health_router
from settings import settings

app = FastAPI()
app.include_router(health_router, prefix=settings.api_prefix)
