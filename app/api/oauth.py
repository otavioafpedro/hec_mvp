from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from urllib.parse import urlencode, urlparse, urlunparse, parse_qsl

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.auth import login_or_create_social_user
from app.config import settings
from app.db.session import get_db

router = APIRouter(prefix="/oauth", tags=["OAuth"])

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"

LINKEDIN_AUTH_URL = "https://www.linkedin.com/oauth/v2/authorization"
LINKEDIN_TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
LINKEDIN_USERINFO_URL = "https://api.linkedin.com/v2/userinfo"


def _format_oauth_error(exc: Exception) -> str:
    raw = str(exc).strip() or exc.__class__.__name__
    lowered = raw.lower()

    if "wallets.wallet_address" in lowered and "does not exist" in lowered:
        return (
            "Banco desatualizado para OAuth social: falta a coluna wallets.wallet_address. "
            "Rode a migration 020_inventory_custody_retirement com 'alembic upgrade head' "
            "no banco usado pelo backend."
        )

    return raw


def _provider_or_404(provider: str) -> str:
    normalized = (provider or "").strip().lower()
    if normalized not in {"google", "linkedin"}:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Provider OAuth nao suportado: {provider}",
        )
    return normalized


def _base64_url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _base64_url_decode(raw: str) -> bytes:
    padding = "=" * (-len(raw) % 4)
    return base64.urlsafe_b64decode(f"{raw}{padding}")


def _encode_state(provider: str, frontend_redirect_uri: str) -> str:
    payload = {
        "provider": provider,
        "frontend_redirect_uri": frontend_redirect_uri,
        "iat": int(time.time()),
    }
    payload_raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    payload_b64 = _base64_url_encode(payload_raw)
    signature = hmac.new(
        settings.OAUTH_STATE_SECRET.encode(),
        payload_b64.encode(),
        hashlib.sha256,
    ).hexdigest()
    return f"{payload_b64}.{signature}"


def _decode_state(provider: str, state_token: str) -> dict:
    parts = state_token.split(".")
    if len(parts) != 2:
        raise ValueError("state malformado")

    payload_b64, signature = parts
    expected = hmac.new(
        settings.OAUTH_STATE_SECRET.encode(),
        payload_b64.encode(),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise ValueError("assinatura de state invalida")

    payload = json.loads(_base64_url_decode(payload_b64).decode())
    iat = int(payload.get("iat", 0))
    if iat <= 0:
        raise ValueError("iat ausente no state")

    age = int(time.time()) - iat
    if age < 0 or age > settings.OAUTH_STATE_TTL_SECONDS:
        raise ValueError("state expirado")

    state_provider = str(payload.get("provider", "")).strip().lower()
    if state_provider != provider:
        raise ValueError("provider no state nao confere")

    redirect_uri = str(payload.get("frontend_redirect_uri", "")).strip()
    if not _is_valid_frontend_redirect_uri(redirect_uri):
        raise ValueError("redirect_uri invalida no state")

    return payload


def _is_valid_frontend_redirect_uri(uri: str) -> bool:
    if not uri:
        return False
    parsed = urlparse(uri)
    if parsed.scheme not in {"http", "https"}:
        return False
    if not parsed.netloc:
        return False

    allowed_hosts = {
        host.strip().lower()
        for host in settings.OAUTH_ALLOWED_REDIRECT_HOSTS.split(",")
        if host.strip()
    }
    if not allowed_hosts:
        return True
    host = (parsed.hostname or "").lower()
    return host in allowed_hosts


def _resolve_frontend_redirect_uri(candidate: str | None) -> str:
    value = (candidate or "").strip()
    if value and _is_valid_frontend_redirect_uri(value):
        return value

    fallback = (settings.OAUTH_DEFAULT_FRONTEND_REDIRECT_URI or "").strip()
    if fallback and _is_valid_frontend_redirect_uri(fallback):
        return fallback

    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Redirect URI de frontend nao configurada para OAuth social",
    )


def _append_query_params(url: str, params: dict[str, str]) -> str:
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.update({k: v for k, v in params.items() if v is not None and v != ""})
    return urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            urlencode(query),
            parsed.fragment,
        )
    )


def _ensure_setting(value: str, key: str) -> str:
    cleaned = (value or "").strip()
    if not cleaned:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Configuracao OAuth ausente: {key}",
        )
    return cleaned


def _build_provider_authorize_url(provider: str, state_token: str) -> str:
    if provider == "google":
        client_id = _ensure_setting(settings.GOOGLE_CLIENT_ID, "GOOGLE_CLIENT_ID")
        redirect_uri = _ensure_setting(settings.GOOGLE_REDIRECT_URI, "GOOGLE_REDIRECT_URI")
        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state_token,
            "access_type": "online",
            "prompt": "select_account",
        }
        return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"

    client_id = _ensure_setting(settings.LINKEDIN_CLIENT_ID, "LINKEDIN_CLIENT_ID")
    redirect_uri = _ensure_setting(settings.LINKEDIN_REDIRECT_URI, "LINKEDIN_REDIRECT_URI")
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": "openid profile email",
        "state": state_token,
    }
    return f"{LINKEDIN_AUTH_URL}?{urlencode(params)}"


def _exchange_google_code(code: str) -> dict:
    response = httpx.post(
        GOOGLE_TOKEN_URL,
        data={
            "code": code,
            "client_id": _ensure_setting(settings.GOOGLE_CLIENT_ID, "GOOGLE_CLIENT_ID"),
            "client_secret": _ensure_setting(settings.GOOGLE_CLIENT_SECRET, "GOOGLE_CLIENT_SECRET"),
            "redirect_uri": _ensure_setting(settings.GOOGLE_REDIRECT_URI, "GOOGLE_REDIRECT_URI"),
            "grant_type": "authorization_code",
        },
        timeout=20,
    )
    if response.status_code >= 400:
        raise ValueError(f"Falha no token Google: HTTP {response.status_code}")
    payload = response.json()
    access_token = payload.get("access_token")
    if not access_token:
        raise ValueError("Resposta Google sem access_token")

    userinfo = httpx.get(
        GOOGLE_USERINFO_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=20,
    )
    if userinfo.status_code >= 400:
        raise ValueError(f"Falha no userinfo Google: HTTP {userinfo.status_code}")
    profile = userinfo.json()
    email = str(profile.get("email") or "").strip().lower()
    if not email:
        raise ValueError("Google nao retornou email")
    return {
        "email": email,
        "name": str(profile.get("name") or "").strip(),
        "sub": str(profile.get("sub") or "").strip(),
    }


def _exchange_linkedin_code(code: str) -> dict:
    response = httpx.post(
        LINKEDIN_TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": _ensure_setting(settings.LINKEDIN_REDIRECT_URI, "LINKEDIN_REDIRECT_URI"),
            "client_id": _ensure_setting(settings.LINKEDIN_CLIENT_ID, "LINKEDIN_CLIENT_ID"),
            "client_secret": _ensure_setting(settings.LINKEDIN_CLIENT_SECRET, "LINKEDIN_CLIENT_SECRET"),
        },
        timeout=20,
    )
    if response.status_code >= 400:
        raise ValueError(f"Falha no token LinkedIn: HTTP {response.status_code}")
    payload = response.json()
    access_token = payload.get("access_token")
    if not access_token:
        raise ValueError("Resposta LinkedIn sem access_token")

    userinfo = httpx.get(
        LINKEDIN_USERINFO_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=20,
    )
    if userinfo.status_code >= 400:
        raise ValueError(f"Falha no userinfo LinkedIn: HTTP {userinfo.status_code}")
    profile = userinfo.json()
    email = str(profile.get("email") or "").strip().lower()
    if not email:
        raise ValueError("LinkedIn nao retornou email")

    name = str(profile.get("name") or "").strip()
    if not name:
        given_name = str(profile.get("given_name") or "").strip()
        family_name = str(profile.get("family_name") or "").strip()
        name = f"{given_name} {family_name}".strip()

    return {
        "email": email,
        "name": name,
        "sub": str(profile.get("sub") or "").strip(),
    }


@router.get(
    "/{provider}/start",
    summary="Inicia OAuth social (Google/LinkedIn)",
)
def start_social_auth(
    provider: str,
    redirect_uri: str | None = Query(
        default=None,
        description="URI de callback no frontend (ex.: https://app/auth/social/callback)",
    ),
):
    normalized_provider = _provider_or_404(provider)
    frontend_redirect_uri = _resolve_frontend_redirect_uri(redirect_uri)
    state_token = _encode_state(normalized_provider, frontend_redirect_uri)
    authorize_url = _build_provider_authorize_url(normalized_provider, state_token)
    return RedirectResponse(url=authorize_url, status_code=status.HTTP_307_TEMPORARY_REDIRECT)


@router.get(
    "/{provider}/callback",
    summary="Callback OAuth social (Google/LinkedIn)",
)
def social_callback(
    provider: str,
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    error_description: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    normalized_provider = _provider_or_404(provider)
    frontend_redirect_uri = _resolve_frontend_redirect_uri(None)

    if state:
        try:
            state_payload = _decode_state(normalized_provider, state)
            frontend_redirect_uri = state_payload["frontend_redirect_uri"]
        except Exception:
            frontend_redirect_uri = _resolve_frontend_redirect_uri(None)

    if error:
        redirect = _append_query_params(
            frontend_redirect_uri,
            {
                "error": "oauth_provider_error",
                "error_description": error_description or error,
                "provider": normalized_provider,
            },
        )
        return RedirectResponse(url=redirect, status_code=status.HTTP_302_FOUND)

    if not code or not state:
        redirect = _append_query_params(
            frontend_redirect_uri,
            {
                "error": "oauth_callback_invalid",
                "error_description": "code/state ausentes",
                "provider": normalized_provider,
            },
        )
        return RedirectResponse(url=redirect, status_code=status.HTTP_302_FOUND)

    try:
        state_payload = _decode_state(normalized_provider, state)
        frontend_redirect_uri = state_payload["frontend_redirect_uri"]
    except Exception as exc:
        redirect = _append_query_params(
            frontend_redirect_uri,
            {
                "error": "oauth_state_invalid",
                "error_description": str(exc),
                "provider": normalized_provider,
            },
        )
        return RedirectResponse(url=redirect, status_code=status.HTTP_302_FOUND)

    try:
        if normalized_provider == "google":
            profile = _exchange_google_code(code)
        else:
            profile = _exchange_linkedin_code(code)

        user, wallet, token, created = login_or_create_social_user(
            db=db,
            email=profile["email"],
            name=profile["name"],
            role="buyer",
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        redirect = _append_query_params(
            frontend_redirect_uri,
            {
                "error": "oauth_login_failed",
                "error_description": _format_oauth_error(exc),
                "provider": normalized_provider,
            },
        )
        return RedirectResponse(url=redirect, status_code=status.HTTP_302_FOUND)

    redirect = _append_query_params(
        frontend_redirect_uri,
        {
            "token": token,
            "email": user.email,
            "name": user.name,
            "role": user.role,
            "provider": normalized_provider,
            "created": "1" if created else "0",
            "wallet_balance_brl": f"{float(wallet.balance_brl):.2f}",
        },
    )
    return RedirectResponse(url=redirect, status_code=status.HTTP_302_FOUND)
