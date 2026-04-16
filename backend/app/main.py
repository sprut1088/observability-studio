from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from backend.app.routes.systems import router as systems_router
from backend.app.routes.export import router as export_router
from backend.app.routes.assess import router as assess_router
from backend.app.routes.download import router as download_router

app = FastAPI(title="ObservaScore UI API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://20.193.248.157:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(systems_router, prefix="/api")
app.include_router(export_router, prefix="/api")
app.include_router(assess_router, prefix="/api")
app.include_router(download_router, prefix="/api")

@app.get("/api/health")
def health():
    return {"status": "ok"}
