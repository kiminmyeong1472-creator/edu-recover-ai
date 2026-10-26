"""Authenticated prepaid gateway; callers cannot select an account or supplier."""
from __future__ import annotations

import sqlite3
from contextlib import closing
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator

from app.platform_core.life_auth import require_life_user
from app.services.owner_basic_auth import _same_origin, require_owner
from app.services import qrng_prepaid as ledger
from app.services.secret_vault import state_root

_HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}


class _BoundedRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def bounded(request: Request):
            if request.method == "POST":
                body = bytearray()
                async for chunk in request.stream():
                    if len(body) + len(chunk) > 4096:
                        return JSONResponse({"detail": "QRNG_INPUT_TOO_LARGE"}, 413, headers=_HEADERS)
                    body.extend(chunk)
                request._body = bytes(body)
            try:
                return await handler(request)
            except RequestValidationError:
                return JSONResponse({"detail": "QRNG_INPUT_INVALID"}, 422, headers=_HEADERS)
            except ledger.QuotaError as exc:
                return JSONResponse({"detail": exc.code}, exc.status, headers=_HEADERS)

        return bounded


router = APIRouter(prefix="/life/api/qrng", route_class=_BoundedRoute, include_in_schema=False)
owner_router = APIRouter(prefix="/owner/qrng", dependencies=[Depends(require_owner)],
                         route_class=_BoundedRoute, include_in_schema=False)

Identifier = Annotated[StrictStr, Field(pattern=r"^[A-Za-z0-9_-]{8,80}$")]


class NumbersRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    request_id: Identifier
    length: Annotated[StrictInt, Field(ge=1, le=1024)] = 1


class PaymentReference(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    provider: Annotated[StrictStr, Field(pattern=r"^[a-z0-9_-]{2,40}$")]
    reference: Annotated[StrictStr, Field(min_length=1, max_length=160)]


class VerifiedPrepayment(PaymentReference):
    account_id: Identifier
    calls: Annotated[StrictInt, Field(ge=1, le=1_000_000)]
    amount_minor: Annotated[StrictInt, Field(ge=1, le=10**12)]
    currency: Annotated[StrictStr, Field(pattern=r"^[A-Z]{3}$")]
    settlement_checked: Literal[True]

    @field_validator("settlement_checked", mode="before")
    @classmethod
    def explicit_check(cls, value):
        if value is not True:
            raise ValueError("manual_settlement_check_required")
        return value


@router.get("/quota")
def quota(account_id: str = Depends(require_life_user)):
    return JSONResponse(ledger.balance(account_id), headers=_HEADERS)


@router.post("/numbers")
def numbers(payload: NumbersRequest, request: Request,
            account_id: str = Depends(require_life_user)):
    if not _same_origin(request):
        raise HTTPException(status_code=403, detail="same_origin_required", headers=_HEADERS)
    result = ledger.request_numbers(account_id=account_id, request_id=payload.request_id,
                                    length=payload.length)
    return JSONResponse(result, headers=_HEADERS)


def _require_existing_account(account_id: str) -> None:
    path = state_root() / "liferecover" / "accounts.sqlite3"
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)) as db:
            exists = db.execute("SELECT 1 FROM account WHERE account_id=?", (account_id,)).fetchone()
    except (sqlite3.Error, OSError):
        raise ledger.QuotaError("QRNG_ACCOUNT_STORAGE_UNAVAILABLE", 503) from None
    if not exists:
        raise ledger.QuotaError("QRNG_ACCOUNT_NOT_FOUND", 404)


@owner_router.post("/prepayments")
def grant(payload: VerifiedPrepayment):
    _require_existing_account(payload.account_id)
    result = ledger.grant_verified_prepayment(**payload.model_dump(exclude={"settlement_checked"}))
    return JSONResponse(result, headers=_HEADERS)


@owner_router.post("/prepayments/revoke")
def revoke(payload: PaymentReference):
    return JSONResponse(ledger.revoke_prepayment(**payload.model_dump()), headers=_HEADERS)
