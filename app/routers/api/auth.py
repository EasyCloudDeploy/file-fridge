"""Authentication API routes."""

import logging
from datetime import timedelta
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app import schemas
from app.config import settings
from app.database import get_db
from app.models import User
from app.security import (
    authenticate_user,
    create_access_token,
    get_current_user,
    hash_password,
    verify_password,
)
from app.utils.rate_limiter import check_login_rate_limit

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/auth", tags=["authentication"])


@router.get("/check", response_model=schemas.AuthCheckResponse)
def check_auth_status(db: Annotated[Session, Depends(get_db)]):
    """
    Check if initial setup is required.

    Returns whether any users exist in the system.
    Used by the frontend to determine whether to show setup or login form.
    """
    user_count = db.query(func.count(User.id)).scalar()
    setup_required = user_count == 0

    from app.services.instance_config_service import instance_config_service

    oidc_enabled = instance_config_service.get_oidc_enabled(db)
    oidc_provider_name = instance_config_service.get_oidc_provider_name(db)

    return schemas.AuthCheckResponse(
        setup_required=setup_required,
        user_count=user_count,
        oidc_enabled=oidc_enabled,
        oidc_provider_name=oidc_provider_name,
    )


@router.post(
    "/setup",
    response_model=schemas.Token,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"description": "Setup already completed or username taken"},
        500: {"description": "Internal server error"},
    },
)
def setup_first_user(user_data: schemas.UserCreate, db: Annotated[Session, Depends(get_db)]):
    """
    Create the first administrator account.

    This endpoint only works when no users exist in the system.
    Once the first user is created, use /login instead.

    Args:
        user_data: Username and password for the first user
        db: Database session

    Returns:
        JWT access token

    Raises:
        HTTPException: 400 if users already exist or username is taken
    """
    # Check if any users exist
    user_count = db.query(func.count(User.id)).scalar()
    if user_count > 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Setup has already been completed. Use /login instead.",
        )

    # Check if username is already taken (shouldn't happen but just in case)
    existing_user = db.query(User).filter(User.username == user_data.username).first()
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Username '{user_data.username}' is already taken",
        )

    # Create first user as admin
    user = User(
        username=user_data.username,
        password_hash=hash_password(user_data.password),
        is_active=True,
        roles=["admin"],
    )

    try:
        db.add(user)
        db.commit()
        db.refresh(user)
        logger.info(f"First user created: {user.username}")
    except Exception:
        db.rollback()
        logger.exception("Failed to create first user")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create user account",
        ) from None

    # Generate and return access token with roles
    access_token = create_access_token(data={"sub": user.username, "roles": user.roles})
    return schemas.Token(access_token=access_token, token_type="bearer")


@router.post(
    "/change-password",
    responses={
        400: {"description": "Incorrect old password"},
        500: {"description": "Internal server error"},
    },
)
def change_password(
    password_data: schemas.PasswordChange,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Change the current user's password.

    Args:
        password_data: Old and new password
        current_user: Currently authenticated user
        db: Database session

    Returns:
        Success message

    Raises:
        HTTPException: 400 if old password is incorrect
    """

    # Verify old password
    if not verify_password(password_data.old_password, current_user.password_hash):
        logger.warning(f"Failed password change attempt for user: {current_user.username}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Incorrect password",
        )

    # Update password
    current_user.password_hash = hash_password(password_data.new_password)

    try:
        db.commit()
        logger.info(f"Password changed for user: {current_user.username}")
        return {"message": "Password changed successfully"}
    except Exception:
        db.rollback()
        logger.exception("Failed to change password")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to change password",
        ) from None


@router.post(
    "/login",
    response_model=schemas.Token,
    dependencies=[Depends(check_login_rate_limit)],
    responses={401: {"description": "Incorrect username or password"}},
)
def login(credentials: schemas.UserLogin, db: Annotated[Session, Depends(get_db)]):
    """
    Authenticate a user and return an access token.

    Args:
        credentials: Username and password
        db: Database session

    Returns:
        JWT access token

    Raises:
        HTTPException: 401 if credentials are invalid
    """
    # Authenticate user
    user = authenticate_user(db, credentials.username, credentials.password)
    if not user:
        logger.warning(f"Failed login attempt for username: {credentials.username}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Generate and return access token with roles
    access_token = create_access_token(data={"sub": user.username, "roles": user.roles})
    logger.info(f"User logged in: {user.username}")
    return schemas.Token(access_token=access_token, token_type="bearer")


@router.post("/tokens", response_model=schemas.Token)
def generate_api_token(
    token_data: schemas.TokenCreate,
    current_user: Annotated[User, Depends(get_current_user)],
):
    """
    Generate a manual API token with custom expiration.

    This endpoint requires authentication.
    Use this to generate tokens for external scripts or API access.

    Args:
        token_data: Token configuration (expiration)
        current_user: Currently authenticated user

    Returns:
        JWT access token

    Note:
        - expires_days = None: Use default expiration (from settings)
        - expires_days = 0: No expiration (token never expires)
        - expires_days > 0: Custom expiration in days
    """
    # Determine expiration
    if token_data.expires_days is None:
        # Use default
        expires_delta = timedelta(days=settings.access_token_expire_days)
        logger.info(
            f"User {current_user.username} generated token with default expiration "
            f"({settings.access_token_expire_days} days)"
        )
    elif token_data.expires_days == 0:
        # No expiration
        expires_delta = timedelta(days=365 * 100)  # 100 years (effectively no expiration)
        logger.warning(f"User {current_user.username} generated token with NO expiration")
    else:
        # Custom expiration
        expires_delta = timedelta(days=token_data.expires_days)
        logger.info(
            f"User {current_user.username} generated token with custom expiration "
            f"({token_data.expires_days} days)"
        )

    # Generate and return access token with roles
    access_token = create_access_token(
        data={"sub": current_user.username, "roles": current_user.roles},
        expires_delta=expires_delta,
    )
    return schemas.Token(access_token=access_token, token_type="bearer")


@router.get("/oidc/login")
async def oidc_login(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
):
    """
    Initiate the OIDC authorization code flow.
    """
    import secrets
    from urllib.parse import urlencode
    from fastapi.responses import RedirectResponse
    from app.services.instance_config_service import instance_config_service

    # Check if OIDC is enabled
    if not instance_config_service.get_oidc_enabled(db):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="OIDC authentication is not enabled",
        )

    # Get issuer URL
    issuer = instance_config_service.get_oidc_issuer(db)
    if not issuer:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="OIDC Issuer URL is not configured",
        )

    # Fetch discovery configuration
    import httpx
    issuer = issuer.rstrip("/")
    discovery_url = f"{issuer}/.well-known/openid-configuration"

    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(discovery_url, timeout=10.0)
            r.raise_for_status()
            config = r.json()
    except Exception as e:
        logger.exception("Failed to fetch OIDC configuration from provider")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch OIDC discovery document: {e!s}",
        ) from None

    authorization_endpoint = config.get("authorization_endpoint")
    if not authorization_endpoint:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="OIDC provider does not expose authorization_endpoint",
        )

    # Get client ID
    client_id = instance_config_service.get_oidc_client_id(db)
    if not client_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="OIDC Client ID is not configured",
        )

    # Determine redirect URI
    redirect_uri = instance_config_service.get_oidc_redirect_uri(db)
    if not redirect_uri:
        proto = request.headers.get("x-forwarded-proto", request.url.scheme)
        host = request.headers.get("x-forwarded-host", request.url.netloc)
        redirect_uri = f"{proto}://{host}/api/v1/auth/oidc/callback"

    # Generate state for CSRF protection
    state = secrets.token_hex(16)

    # Construct redirect URL
    params = {
        "client_id": client_id,
        "response_type": "code",
        "scope": "openid profile email",
        "redirect_uri": redirect_uri,
        "state": state,
    }
    auth_url = f"{authorization_endpoint}?{urlencode(params)}"

    response = RedirectResponse(auth_url)
    response.set_cookie(
        key="oidc_state",
        value=state,
        httponly=True,
        secure=request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https",
        samesite="lax",
        max_age=300,  # 5 minutes
    )
    return response


def _verify_oidc_callback_state(
    request: Request,
    state: Optional[str],
    code: Optional[str],
    error: Optional[str],
    error_description: Optional[str],
) -> str:
    """Validate query parameters and state cookie for OIDC callback."""
    if error:
        error_desc = error_description or error
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"OIDC authentication failed: {error_desc}",
        )

    if not code or not state:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing required OIDC query parameters (code/state)",
        )

    cookie_state = request.cookies.get("oidc_state")
    if not cookie_state or cookie_state != state:
        logger.warning(f"OIDC state mismatch. cookie: {cookie_state}, query: {state}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="State verification failed (CSRF check failed). Please try logging in again.",
        )
    return code


async def _get_oidc_endpoints(discovery_url: str) -> tuple[str, str]:
    """Fetch OIDC endpoints from discovery URL."""
    import httpx
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(discovery_url, timeout=10.0)
            r.raise_for_status()
            config = r.json()
    except Exception as e:
        logger.exception("Failed to fetch OIDC discovery document")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to reach OIDC provider: {e!s}",
        ) from None

    token_endpoint = config.get("token_endpoint")
    userinfo_endpoint = config.get("userinfo_endpoint")

    if not token_endpoint or not userinfo_endpoint:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="OIDC provider does not expose token/userinfo endpoints",
        )
    return token_endpoint, userinfo_endpoint


async def _exchange_code_for_tokens(
    token_endpoint: str,
    code: str,
    redirect_uri: str,
    client_id: str,
    client_secret: Optional[str],
) -> dict:
    """Exchange authorization code for access/ID tokens."""
    import httpx
    token_data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
    }
    if client_secret:
        token_data["client_secret"] = client_secret

    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(token_endpoint, data=token_data, timeout=10.0)
            r.raise_for_status()
            tokens = r.json()
    except Exception as e:
        logger.exception("Failed to exchange code for OIDC tokens")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"OIDC token exchange failed: {e!s}",
        ) from None

    if not tokens.get("access_token"):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="OIDC provider did not return access_token",
        )
    return tokens


async def _fetch_userinfo(userinfo_endpoint: str, access_token: str) -> dict:
    """Fetch user information from OIDC provider userinfo endpoint."""
    import httpx
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                userinfo_endpoint,
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=10.0,
            )
            r.raise_for_status()
            return r.json()
    except Exception as e:
        logger.exception("Failed to fetch userinfo from OIDC provider")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch user info from OIDC provider: {e!s}",
        ) from None


def _extract_oidc_roles(userinfo: dict, tokens: dict, roles_claim: str) -> list[str]:
    """Extract OIDC roles/groups from userinfo or ID token payload (safe from signature warnings)."""
    oidc_roles = userinfo.get(roles_claim)

    if not oidc_roles and "id_token" in tokens:
        try:
            import base64
            import json

            parts = tokens["id_token"].split(".")
            if len(parts) >= 2:
                payload_b64 = parts[1]
                # Fix base64 padding
                payload_b64 += "=" * ((4 - len(payload_b64) % 4) % 4)
                payload_bytes = base64.urlsafe_b64decode(payload_b64.encode("utf-8"))
                payload = json.loads(payload_bytes.decode("utf-8"))
                oidc_roles = payload.get(roles_claim)
        except Exception:
            logger.warning("Failed to decode id_token payload to extract roles")

    # Normalize roles
    if oidc_roles is None:
        return []
    if isinstance(oidc_roles, str):
        return [r.strip() for r in oidc_roles.split(",") if r.strip()]
    if isinstance(oidc_roles, list):
        return [str(r) for r in oidc_roles]
    return []


def _map_roles(
    oidc_roles: list[str],
    admin_group: str,
    manager_group: str,
    viewer_group: str,
    default_roles_str: str,
) -> list[str]:
    """Map OIDC groups to File Fridge local roles."""
    mapped_roles = set()
    for r in oidc_roles:
        r_lower = str(r).lower()
        if admin_group and r_lower == admin_group.lower():
            mapped_roles.add("admin")
        if manager_group and r_lower == manager_group.lower():
            mapped_roles.add("manager")
        if viewer_group and r_lower == viewer_group.lower():
            mapped_roles.add("viewer")

    if not mapped_roles:
        defaults = [r.strip() for r in default_roles_str.split(",") if r.strip()]
        for d in defaults:
            if d in ["admin", "manager", "viewer"]:
                mapped_roles.add(d)
        if not mapped_roles:
            mapped_roles.add("viewer")

    return list(mapped_roles)


def _sync_local_user(db: Session, username: str, roles: list[str]) -> User:
    """Find or create local user and update their roles."""
    user = db.query(User).filter(User.username == username).first()
    if not user:
        import secrets
        random_password = secrets.token_urlsafe(32)
        user = User(
            username=username,
            password_hash=hash_password(random_password),
            is_active=True,
            roles=roles,
        )
        db.add(user)
        logger.info(f"OIDC: Created new user '{username}' with roles {roles}")
    else:
        user.roles = roles
        logger.info(f"OIDC: Updated user '{username}' roles to {roles}")

    try:
        db.commit()
        db.refresh(user)
    except Exception:
        db.rollback()
        logger.exception("Failed to save OIDC user to database")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to save user account",
        ) from None

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is inactive",
        )
    return user


@router.get("/oidc/callback", response_class=HTMLResponse)
async def oidc_callback(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    error_description: Optional[str] = None,
):
    """
    Handle the OIDC authorization code callback.
    """
    from fastapi.responses import HTMLResponse
    from app.services.instance_config_service import instance_config_service

    # 1. Verify callback parameters and state cookie
    code = _verify_oidc_callback_state(request, state, code, error, error_description)

    # 2. Get server OIDC configurations
    issuer = instance_config_service.get_oidc_issuer(db)
    client_id = instance_config_service.get_oidc_client_id(db)
    client_secret = instance_config_service.get_oidc_client_secret(db)

    if not issuer or not client_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="OIDC is not fully configured on the server",
        )

    # 3. Retrieve provider endpoints
    issuer = issuer.rstrip("/")
    discovery_url = f"{issuer}/.well-known/openid-configuration"
    token_endpoint, userinfo_endpoint = await _get_oidc_endpoints(discovery_url)

    # 4. Determine redirect URI
    redirect_uri = instance_config_service.get_oidc_redirect_uri(db)
    if not redirect_uri:
        proto = request.headers.get("x-forwarded-proto", request.url.scheme)
        host = request.headers.get("x-forwarded-host", request.url.netloc)
        redirect_uri = f"{proto}://{host}/api/v1/auth/oidc/callback"

    # 5. Exchange code for tokens
    tokens = await _exchange_code_for_tokens(
        token_endpoint, code, redirect_uri, client_id, client_secret
    )

    # 6. Fetch user info claims
    userinfo = await _fetch_userinfo(userinfo_endpoint, tokens["access_token"])

    # 7. Extract username
    username = (
        userinfo.get("preferred_username")
        or userinfo.get("nickname")
        or userinfo.get("name")
        or userinfo.get("email")
        or userinfo.get("sub")
    )
    if not username:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to extract a valid username from userinfo claims",
        )
    username = username.strip().lower()

    # 8. Extract roles and map to local roles
    roles_claim = instance_config_service.get_oidc_roles_claim(db)
    oidc_roles = _extract_oidc_roles(userinfo, tokens, roles_claim)

    admin_group = instance_config_service.get_oidc_admin_group(db)
    manager_group = instance_config_service.get_oidc_manager_group(db)
    viewer_group = instance_config_service.get_oidc_viewer_group(db)
    default_roles_str = instance_config_service.get_oidc_default_roles(db)

    mapped_roles = _map_roles(
        oidc_roles, admin_group, manager_group, viewer_group, default_roles_str
    )

    # 9. Sync or provision user in local database
    user = _sync_local_user(db, username, mapped_roles)

    # 10. Generate access token
    local_access_token = create_access_token(data={"sub": user.username, "roles": user.roles})

    # 11. Render landing page response
    html_content = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Authenticating...</title>
    <script>
        try {{
            sessionStorage.setItem('auth_token', '{local_access_token}');
            window.location.href = '/';
        }} catch (e) {{
            console.error('Failed to save auth token:', e);
            document.body.innerHTML = '<p style="color: red;">Failed to complete login. Please enable session storage.</p>';
        }}
    </script>
</head>
<body>
    <p style="font-family: sans-serif; text-align: center; margin-top: 50px;">Logging in, please wait...</p>
</body>
</html>"""

    response = HTMLResponse(content=html_content)
    response.delete_cookie(key="oidc_state")
    return response
