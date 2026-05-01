from flask import Blueprint, request, jsonify, g
from app import db
from app.models import User, Role, RefreshToken
from app.middleware.auth import require_role, require_auth, hash_password
from app.utils.validators import validate_create_user, EMAIL_RE

users_admin_bp = Blueprint("users_admin", __name__)

VALID_ROLES = {"admin", "marketer", "viewer"}
VALID_STATUSES = {"active", "inactive"}


def _next_user_id() -> str:
    from sqlalchemy import func
    count = User.query.count()
    return f"U{count + 1:03d}"


# ─── POST /api/admin/users ─────────────────────────────────────────────────────

@users_admin_bp.route("", methods=["POST"])
@require_role("admin")
def create_user():
    """
    Create a new user. Admin only.
    ---
    tags:
      - User Management (Admin)
    security:
      - Bearer: []
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Bad Request", "message": "JSON body required"}), 400

    errors = validate_create_user(data)
    if errors:
        return jsonify({"error": "Bad Request", "message": errors}), 400

    email = data["email"].strip().lower()
    if User.query.filter_by(email=email).first():
        return jsonify({"error": "Conflict", "message": f"User with email '{email}' already exists"}), 409

    role_name = data["role"].strip().lower()
    role = Role.query.filter_by(name=role_name).first()
    if not role:
        return jsonify({"error": "Bad Request", "message": f"Role '{role_name}' not found"}), 400

    user = User(
        user_id=_next_user_id(),
        email=email,
        password_hash=hash_password(data["password"]),
        first_name=data.get("first_name", "").strip() or None,
        last_name=data.get("last_name", "").strip() or None,
        role_id=role.id,
        status="active",
        must_change_password=data.get("must_change_password", True),
    )
    db.session.add(user)
    db.session.commit()

    return jsonify({"message": "User created", "user": user.to_dict()}), 201


# ─── GET /api/admin/users ──────────────────────────────────────────────────────

@users_admin_bp.route("", methods=["GET"])
@require_role("admin")
def list_users():
    """List all users with optional filters. Admin only."""
    try:
        page = max(1, int(request.args.get("page", 1)))
        per_page = min(100, max(1, int(request.args.get("per_page", 20))))
    except ValueError:
        return jsonify({"error": "Bad Request", "message": "page and per_page must be integers"}), 400

    query = User.query

    role_filter = request.args.get("role", "").strip().lower()
    if role_filter:
        if role_filter not in VALID_ROLES:
            return jsonify({"error": "Bad Request", "message": f"Invalid role. Choose from: {sorted(VALID_ROLES)}"}), 400
        role = Role.query.filter_by(name=role_filter).first()
        if role:
            query = query.filter(User.role_id == role.id)

    status_filter = request.args.get("status", "").strip().lower()
    if status_filter:
        if status_filter not in VALID_STATUSES:
            return jsonify({"error": "Bad Request", "message": f"Invalid status. Choose from: {sorted(VALID_STATUSES)}"}), 400
        query = query.filter(User.status == status_filter)

    pagination = query.order_by(User.user_id).paginate(page=page, per_page=per_page, error_out=False)

    return jsonify({
        "users": [u.to_dict() for u in pagination.items],
        "pagination": {
            "page": page,
            "per_page": per_page,
            "total": pagination.total,
            "pages": pagination.pages,
        },
    }), 200


# ─── GET /api/admin/users/<user_id> ───────────────────────────────────────────

@users_admin_bp.route("/<user_id>", methods=["GET"])
@require_role("admin")
def get_user(user_id):
    """Get a single user by ID. Admin only."""
    user = db.session.get(User, user_id.upper())
    if not user:
        return jsonify({"error": "Not Found", "message": f"User '{user_id}' not found"}), 404
    return jsonify({"user": user.to_dict()}), 200


# ─── PUT /api/admin/users/<user_id> ───────────────────────────────────────────

@users_admin_bp.route("/<user_id>", methods=["PUT"])
@require_role("admin")
def update_user(user_id):
    """
    Update a user's role and/or status. Admin only.
    ---
    tags:
      - User Management (Admin)
    security:
      - Bearer: []
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Bad Request", "message": "JSON body required"}), 400

    user = db.session.get(User, user_id.upper())
    if not user:
        return jsonify({"error": "Not Found", "message": f"User '{user_id}' not found"}), 404

    # Prevent admin from deactivating themselves
    if user.user_id == g.current_user.user_id and data.get("status") == "inactive":
        return jsonify({"error": "Forbidden", "message": "You cannot deactivate your own account"}), 403

    errors = []

    new_role = data.get("role")
    if new_role is not None:
        if new_role not in VALID_ROLES:
            errors.append(f"role must be one of: {sorted(VALID_ROLES)}")
        else:
            role = Role.query.filter_by(name=new_role).first()
            if not role:
                errors.append(f"Role '{new_role}' not found in database")
            else:
                user.role_id = role.id

    new_status = data.get("status")
    if new_status is not None:
        if new_status not in VALID_STATUSES:
            errors.append(f"status must be one of: {sorted(VALID_STATUSES)}")
        else:
            user.status = new_status
            # Revoke all refresh tokens if deactivating
            if new_status == "inactive":
                RefreshToken.query.filter_by(user_id=user.user_id).update({"revoked": True})

    new_password = data.get("password")
    if new_password:
        if len(new_password) < 8:
            errors.append("password must be at least 8 characters")
        else:
            user.password_hash = hash_password(new_password)
            user.must_change_password = False

    first_name = data.get("first_name")
    if first_name is not None:
        user.first_name = str(first_name).strip() or None

    last_name = data.get("last_name")
    if last_name is not None:
        user.last_name = str(last_name).strip() or None

    if errors:
        return jsonify({"error": "Bad Request", "message": errors}), 400

    db.session.commit()
    return jsonify({"message": "User updated", "user": user.to_dict()}), 200


# ─── DELETE /api/admin/users/<user_id> ────────────────────────────────────────

@users_admin_bp.route("/<user_id>", methods=["DELETE"])
@require_role("admin")
def deactivate_user(user_id):
    """Deactivate a user (soft delete). Admin only."""
    user = db.session.get(User, user_id.upper())
    if not user:
        return jsonify({"error": "Not Found", "message": f"User '{user_id}' not found"}), 404

    if user.user_id == g.current_user.user_id:
        return jsonify({"error": "Forbidden", "message": "You cannot deactivate your own account"}), 403

    if user.status == "inactive":
        return jsonify({"error": "Conflict", "message": f"User '{user_id}' is already inactive"}), 409

    user.status = "inactive"
    RefreshToken.query.filter_by(user_id=user.user_id).update({"revoked": True})
    db.session.commit()

    return jsonify({"message": f"User '{user_id}' has been deactivated", "user": user.to_dict()}), 200
