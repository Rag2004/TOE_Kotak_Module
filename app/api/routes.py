"""Health and order blotter."""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.readiness import liveness, readiness
from app.state_machine import get_all_order_states

router = APIRouter(prefix="/toe", tags=["toe-kotak"])


@router.get("/live")
def live():
    return liveness()


@router.get("/ready")
def ready():
    body = readiness()
    if not body.get("ready"):
        return JSONResponse(status_code=503, content=body)
    return body


@router.get("/health")
def health():
    return ready()


@router.get("/orders")
def get_orders():
    result = []
    for signal_id, transitions in get_all_order_states().items():
        current = transitions[-1]["to"] if transitions else "UNKNOWN"
        result.append(
            {
                "signal_id": signal_id,
                "current_state": current,
                "transitions": len(transitions),
                "history": transitions,
            }
        )
    return result
