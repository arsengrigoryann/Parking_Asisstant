"""Simple bearer-token authentication for the bounded demo administrator API."""

import secrets

from fastapi import HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import SecretStr


def require_admin_token(
    credentials: HTTPAuthorizationCredentials | None,
    configured_token: SecretStr | None,
) -> None:
    """Reject missing/mismatched tokens without echoing secret material."""
    if configured_token is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="administrator API authentication is not configured",
        )
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="administrator authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    supplied = credentials.credentials.encode("utf-8")
    expected = configured_token.get_secret_value().encode("utf-8")
    if not secrets.compare_digest(supplied, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid administrator credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
