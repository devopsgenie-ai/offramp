from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from routes.health import router as health_router
from routes.items import router as items_router

app = FastAPI(title="Widgets")
app.add_middleware(CORSMiddleware, allow_origins=["*"])

api_router = APIRouter(prefix="/api")
api_router.include_router(health_router)
api_router.include_router(items_router)

app.include_router(api_router)

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8001)
