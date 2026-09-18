"""FastAPI application — GridWise energy optimizer."""

from __future__ import annotations

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.models import ScenarioRequest
from app.pipeline import run_pipeline

app = FastAPI(title="GridWise Energy Optimizer", version="0.1.0")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/optimize-energy")
async def optimize_energy(request: Request):
    # Parse raw JSON first to give clean 400s
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            status_code=400,
            content={"error": "Malformed JSON in request body."},
        )

    # Validate with pydantic
    try:
        scenario = ScenarioRequest(**body)
    except ValidationError as exc:
        errors = exc.errors()
        msgs = [f"{e['loc']}: {e['msg']}" for e in errors[:5]]
        return JSONResponse(
            status_code=400,
            content={"error": "Validation failed", "details": msgs},
        )
    except Exception:
        return JSONResponse(
            status_code=400,
            content={"error": "Invalid request structure."},
        )

    # Run pipeline
    try:
        result = await run_pipeline(scenario)
        return JSONResponse(content=result)
    except ValueError as exc:
        return JSONResponse(
            status_code=400,
            content={"error": str(exc)},
        )
    except Exception:
        return JSONResponse(
            status_code=500,
            content={"error": "Internal server error."},
        )


# Global exception handler for anything uncaught
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error."},
    )
