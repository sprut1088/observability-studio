"""
GET /api/feature-flags

Returns the current feature flag state so the UI can conditionally render
accelerator tiles, and so backend routes can enforce disabled accelerators.
"""

from fastapi import APIRouter
from shared_core.feature_flags import load_feature_flags

router = APIRouter()


@router.get(
    "/feature-flags",
    summary="Get platform feature flags",
    description=(
        "Returns a map of accelerator names to booleans. "
        "The UI uses this to show or hide tiles; backend routes use it to "
        "reject calls to disabled accelerators."
    ),
)
def get_feature_flags() -> dict:
    return load_feature_flags()
