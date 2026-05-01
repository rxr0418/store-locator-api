import jwt
import hashlib
import bcrypt
from datetime import datetime, timezone, timedelta
from functools import wraps
from flask import request, jsonify, current_app, g
from app.models import User, RefreshToken
from app import db


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt(12)).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


def generate_access_token(user: User) -> str:
    expires = int(current_app.config["JWT_ACCESS_TOKEN_EXPIRES"])
    now = datetime.now(timezone.utc)
    payload = {
        "user_id": user.user_id,
        "email": user.email,
        "role": user.role.name,
        "exp": int((now + timedelta(seconds=expires)).timestamp()),
        "type": "access",
    }
    return jwt.encode(payload, current_app.config["JWT_SECRET_KEY"], algorithm="HS256")


def generate_refresh_token_value() -> str:
    import secrets
    return secrets.token_urlsafe(64)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def store_refresh_token(user: User, token_value: str) -> RefreshToken:
    expires_seconds = int(current_app.config["JWT_REFRESH_TOKEN_EXPIRES"])
    rt = RefreshToken(
        token_hash=hash_token(token_value),
        user_id=user.user_id,
        expires_at=utcnow() + timedelta(seconds=expires_seconds),
    )
    db.session.add(rt)
    db.session.commit()
    return rt


def decode_access_token(token: str) -> dict:
    return jwt.decode(
        token,
        current_app.config["JWT_SECRET_KEY"],
        algorithms=["HS256"],
        options={"verify_iat": False},
    )


def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify({"error": "Unauthorized", "message": "Missing or invalid Authorization header"}), 401

        token = auth_header.split(" ", 1)[1]
        try:
            payload = decode_access_token(token)
        except jwt.ExpiredSignatureError:
            return jsonify({"error": "Unauthorized", "message": "Access token expired"}), 401
        except jwt.InvalidTokenError as e:
            return jsonify({"error": "Unauthorized", "message": f"Invalid token: {str(e)}"}), 401

        if payload.get("type") != "access":
            return jsonify({"error": "Unauthorized", "message": "Invalid token type"}), 401

        user = db.session.get(User, payload["user_id"])
        if not user or user.status != "active":
            return jsonify({"error": "Unauthorized", "message": "User not found or inactive"}), 401

        g.current_user = user
        g.token_payload = payload
        return f(*args, **kwargs)

    return decorated


def require_permission(*permissions):
    def decorator(f):
        @wraps(f)
        @require_auth
        def decorated(*args, **kwargs):
            user_permissions = g.current_user.role.get_permission_names()
            for perm in permissions:
                if perm not in user_permissions:
                    return jsonify({
                        "error": "Forbidden",
                        "message": f"Required permission '{perm}' not granted for role '{g.current_user.role.name}'",
                    }), 403
            return f(*args, **kwargs)
        return decorated
    return decorator


def require_role(*roles):
    def decorator(f):
        @wraps(f)
        @require_auth
        def decorated(*args, **kwargs):
            if g.current_user.role.name not in roles:
                return jsonify({
                    "error": "Forbidden",
                    "message": f"Role '{g.current_user.role.name}' is not authorized. Required: {list(roles)}",
                }), 403
            return f(*args, **kwargs)
        return decorated
    return decorator
