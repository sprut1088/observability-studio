from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from backend.app.routes.systems import router as systems_router
from backend.app.routes.export import router as export_router
from backend.app.routes.assess import router as assess_router
from backend.app.routes.red_intelligence import router as red_intelligence_router
from backend.app.routes.observability_gap_map import router as observability_gap_map_router
from backend.app.routes.download import router as download_router
from backend.app.routes.v1 import router as v1_router
from backend.app.routes.feature_flags import router as feature_flags_router
from shared_core.feature_flags import load_feature_flags

app = FastAPI(title="Observability Studio API", version="0.3.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://20.193.248.157:5173",
        "http://10.235.21.132:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(systems_router, prefix="/api")
app.include_router(export_router, prefix="/api")
app.include_router(assess_router, prefix="/api")
app.include_router(red_intelligence_router, prefix="/api")
app.include_router(observability_gap_map_router, prefix="/api")
app.include_router(download_router, prefix="/api")
app.include_router(v1_router, prefix="/api")            # Hub v1 — /api/v1/{validate,crawl,assess}
app.include_router(feature_flags_router, prefix="/api") # GET /api/feature-flags


def _require_flag(name: str) -> None:
    """Raise 503 if the named accelerator is disabled in feature_flags.yaml."""
    flags = load_feature_flags()
    if not flags.get(name, True):
        raise HTTPException(
            status_code=503,
            detail=f"Accelerator '{name}' is currently disabled via feature flags.",
        )


@app.middleware("http")
async def enforce_feature_flags(request, call_next):
    """Block requests to disabled accelerator endpoints at the platform level."""
    path = request.url.path

    # ObsCrawl endpoints
    if path in ("/api/v1/crawl", "/api/v1/validate", "/api/export"):
        _require_flag("obscrawl")

    # ObservaScore endpoints
    if path in ("/api/assess", "/api/v1/assess"):
        _require_flag("observascore")

    # RCA Agent endpoint
    if path == "/api/v1/rca":
        _require_flag("rca_agent")

    # RED Panel Intelligence endpoint
    if path == "/api/red-intelligence":
        _require_flag("red_panel_intelligence")

    # Observability Gap Map endpoint
    if path == "/api/observability-gap-map":
        _require_flag("observability_gap_map")

    return await call_next(request)


@app.get("/api/health")
def health():
    return {"status": "ok"}
