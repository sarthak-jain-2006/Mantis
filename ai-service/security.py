import secrets

from fastapi import Header, HTTPException, status

from config import settings


def require_internal_token(x_internal_token: str | None = Header(default=None)) -> None:
    """Reject any request that does not carry the shared Node-to-Python secret."""
    # compare_digest takes the same time whether the first or last character differs,
    # so the token can't be guessed by measuring response times.
    if x_internal_token is None or not secrets.compare_digest(
        x_internal_token, settings.internal_token
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid internal token")
