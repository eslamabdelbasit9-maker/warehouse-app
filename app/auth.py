"""تسجيل الدخول: بالإيميل وكلمة المرور (افتراضي)، أو بحساب Microsoft 365 إذا أُعدّ Entra ID، أو دخول تجريبي للتطوير."""
import hashlib
import hmac
import os

import msal
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from .config import settings
from .db import get_db
from .models import User

router = APIRouter()
SCOPES = ["User.Read"]


class LoginRequired(Exception):
    pass


class Forbidden(Exception):
    pass


def hash_password(pw: str) -> str:
    salt = os.urandom(16)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt, 200_000)
    return f"pbkdf2${salt.hex()}${h.hex()}"


def check_password(pw: str, stored: str | None) -> bool:
    if not stored or not pw:
        return False
    try:
        _, salt, h = stored.split("$")
    except ValueError:
        return False
    calc = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), bytes.fromhex(salt), 200_000)
    return hmac.compare_digest(calc.hex(), h)


def _msal_app():
    return msal.ConfidentialClientApplication(
        settings.ENTRA_CLIENT_ID,
        authority=f"https://login.microsoftonline.com/{settings.ENTRA_TENANT_ID}",
        client_credential=settings.ENTRA_CLIENT_SECRET,
    )


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    uid = request.session.get("uid")
    user = db.get(User, uid) if uid else None
    if not user or not user.active:
        request.session.clear()
        raise LoginRequired(str(request.url.path))
    return user


def require(*roles):
    def dep(user: User = Depends(current_user)):
        if not user.has(*roles):
            raise Forbidden()
        return user
    return dep


@router.get("/login")
def login(request: Request, next: str = "/"):
    request.session["next"] = next if (next.startswith("/") and not next.startswith("//")) else "/"
    if not settings.ENTRA_CLIENT_ID:
        return RedirectResponse("/dev-login" if settings.DEV_AUTH else "/signin")
    flow = _msal_app().initiate_auth_code_flow(SCOPES, redirect_uri=f"{settings.BASE_URL}/auth/callback")
    request.session["flow"] = flow
    return RedirectResponse(flow["auth_uri"])


@router.get("/auth/callback")
def callback(request: Request, db: Session = Depends(get_db)):
    flow = request.session.pop("flow", None)
    if not flow:
        return RedirectResponse("/login")
    result = _msal_app().acquire_token_by_auth_code_flow(flow, dict(request.query_params))
    claims = result.get("id_token_claims") or {}
    email = (claims.get("preferred_username") or claims.get("email") or "").lower()
    if not email:
        return RedirectResponse("/denied")
    user = db.query(User).filter(User.email == email).first()
    if not user or not user.active:
        request.session["denied_email"] = email
        return RedirectResponse("/denied")
    request.session["uid"] = user.id
    return RedirectResponse(request.session.pop("next", "/"))


@router.get("/logout")
def logout(request: Request):
    request.session.clear()
    if settings.ENTRA_CLIENT_ID and not settings.DEV_AUTH:
        return RedirectResponse(
            f"https://login.microsoftonline.com/{settings.ENTRA_TENANT_ID}/oauth2/v2.0/logout"
            f"?post_logout_redirect_uri={settings.BASE_URL}/")
    return RedirectResponse("/login")
