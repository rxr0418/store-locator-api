from flask import Blueprint, request, jsonify, g
from app import db
from app.models import User, RefreshToken
from app.middleware.auth import (
    verify_password, hash_password,
    generate_access_token, generate_refresh_token_value,
    store_refresh_token, hash_token, require_auth,
)
from app.utils.validators import validate_create_user, EMAIL_RE

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/login", methods=["POST"])
def login():
    """
    Login with email/password. Returns access token + refresh token.
    ---
    tags:
      - Authentication
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [email, password]
          properties:
            email:
              type: string
              example: admin@test.com
            password:
              type: string
              example: AdminTest123!
    responses:
      200:
        description: Login successful
      400:
        description: Missing fields
      401:
        description: Invalid credentials
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Bad Request", "message": "JSON body required"}), 400

    email = str(data.get("email", "")).strip().lower()
    password = str(data.get("password", "")).strip()

    if not email or not password:
        return jsonify({"error": "Bad Request", "message": "email and password are required"}), 400

    if not EMAIL_RE.match(email):
        return jsonify({"error": "Bad Request", "message": "Invalid email format"}), 400

    user = User.query.filter_by(email=email).first()

    # Constant-time comparison to avoid timing attacks
    if not user or not verify_password(password, user.password_hash):
        return jsonify({"error": "Unauthorized", "message": "Invalid email or password"}), 401

    if user.status != "active":
        return jsonify({"error": "Unauthorized", "message": "Account is inactive"}), 401

    access_token = generate_access_token(user)
    refresh_token_value = generate_refresh_token_value()
    store_refresh_token(user, refresh_token_value)

    return jsonify({
        "access_token": access_token,
        "refresh_token": refresh_token_value,
        "token_type": "Bearer",
        "expires_in": 900,
        "user": user.to_dict(),
    }), 200


@auth_bp.route("/refresh", methods=["POST"])
def refresh():
    """
    Exchange a valid refresh token for a new access token.
    ---
    tags:
      - Authentication
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [refresh_token]
          properties:
            refresh_token:
              type: string
    responses:
      200:
        description: New access token issued
      400:
        description: Missing refresh token
      401:
        description: Invalid or expired refresh token
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Bad Request", "message": "JSON body required"}), 400

    token_value = data.get("refresh_token", "").strip()
    if not token_value:
        return jsonify({"error": "Bad Request", "message": "refresh_token is required"}), 400

    token_hash = hash_token(token_value)
    rt = RefreshToken.query.filter_by(token_hash=token_hash).first()

    if not rt or not rt.is_valid():
        return jsonify({"error": "Unauthorized", "message": "Invalid or expired refresh token"}), 401

    user = rt.user
    if user.status != "active":
        return jsonify({"error": "Unauthorized", "message": "Account is inactive"}), 401

    new_access_token = generate_access_token(user)

    return jsonify({
        "access_token": new_access_token,
        "token_type": "Bearer",
        "expires_in": 900,
    }), 200


@auth_bp.route("/logout", methods=["POST"])
@require_auth
def logout():
    """
    Revoke the provided refresh token.
    ---
    tags:
      - Authentication
    security:
      - Bearer: []
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [refresh_token]
          properties:
            refresh_token:
              type: string
    responses:
      200:
        description: Logged out
      400:
        description: Missing refresh token
      404:
        description: Token not found
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Bad Request", "message": "JSON body required"}), 400

    token_value = data.get("refresh_token", "").strip()
    if not token_value:
        return jsonify({"error": "Bad Request", "message": "refresh_token is required"}), 400

    token_hash = hash_token(token_value)
    rt = RefreshToken.query.filter_by(token_hash=token_hash, user_id=g.current_user.user_id).first()

    if not rt:
        return jsonify({"error": "Not Found", "message": "Refresh token not found"}), 404

    rt.revoked = True
    db.session.commit()

    return jsonify({"message": "Successfully logged out"}), 200


@auth_bp.route("/me", methods=["GET"])
@require_auth
def me():
    """Get current authenticated user info."""
    return jsonify({"user": g.current_user.to_dict()}), 200
