"""HTTP Basic guard for EduRecover owner-only routes.

Credentials are read from deployment environment variables and are never
returned by the application. Railway HTTPS terminates transport encryption.
"""

from __future__ import annotations

import os
import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

security = HTTPBasic(auto_error=False)


def require_owner(
    credentials: HTTPBasicCredentials | None = Depends(security),
) -> None:
    expected_users = tuple(
        user for user in (
            os.getenv("EDURECOVER_OWNER_USER", ""),
            os.getenv("EDURECOVER_OWNER_USER ", ""),
        ) if user
    )
    expected_password = os.getenv("EDURECOVER_OWNER_PASSWORD", "")
    if not expected_users or not expected_password:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Owner authentication is not configured.",
        )
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Owner authentication required.",
            headers={"WWW-Authenticate": "Basic"},
        )
    ok_user = any(
        secrets.compare_digest(credentials.username, user) for user in expected_users
    )
    ok_password = secrets.compare_digest(credentials.password, expected_password)
    if not (ok_user and ok_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid owner credentials.",
            headers={"WWW-Authenticate": "Basic"},
        )
