import uuid
from flask import Blueprint, request, jsonify, g
from app import db
from app.models import Store, StoreService
from app.middleware.auth import require_auth, require_role
from app.utils.validators import (
    validate_create_store, validate_patch_store,
    VALID_SERVICES, DAY_KEYS,
)
from app.services.geo_service import geocode_address
from app.services.import_service import process_csv_import

stores_admin_bp = Blueprint("stores_admin", __name__)

ALLOWED_STORE_TYPES = {"flagship", "regular", "outlet", "express"}
ALLOWED_STATUSES = {"active", "inactive", "temporarily_closed"}


def _next_store_id() -> str:
    from sqlalchemy import func
    max_id = db.session.query(func.max(Store.store_id)).scalar()
    if max_id and max_id.startswith("S"):
        try:
            num = int(max_id[1:]) + 1
            while db.session.get(Store, f"S{num:04d}"):
                num += 1
            return f"S{num:04d}"
        except ValueError:
            pass
    count = Store.query.count()
    return f"S{count + 1:04d}"


def _apply_services(store_id: str, services: list):
    StoreService.query.filter_by(store_id=store_id).delete()
    for svc in services:
        if svc in VALID_SERVICES:
            db.session.add(StoreService(store_id=store_id, service_name=svc))


def _apply_hours(store: Store, hours: dict):
    day_map = {
        "mon": "hours_mon", "tue": "hours_tue", "wed": "hours_wed",
        "thu": "hours_thu", "fri": "hours_fri", "sat": "hours_sat", "sun": "hours_sun",
    }
    for day, col in day_map.items():
        if day in hours:
            setattr(store, col, hours[day] if hours[day] else None)


# ─── POST /api/admin/stores/import 
@stores_admin_bp.route("/import", methods=["POST"])
@require_role("admin", "marketer")
def import_stores():
    """Batch import stores from CSV file (upsert)."""
    if "file" not in request.files:
        return jsonify({"error": "Bad Request", "message": "No file uploaded. Use 'file' field in multipart/form-data"}), 400

    file = request.files["file"]

    if not file or file.filename == "":
        return jsonify({"error": "Bad Request", "message": "Empty filename — please select a file to upload"}), 400

    if not file.filename.lower().endswith(".csv"):
        return jsonify({"error": "Bad Request", "message": "File must be a CSV (.csv extension)"}), 400

    file_bytes = file.read()
    if len(file_bytes) == 0:
        return jsonify({"error": "Bad Request", "message": "Uploaded file is empty"}), 400

    report = process_csv_import(file_bytes)

    if report["failed"] > 0 and report["failed"] == report["total_rows"]:
        status_code = 422
    elif report["failed"] > 0:
        status_code = 207
    else:
        status_code = 200

    return jsonify({"message": "Import complete", "report": report}), status_code


# ─── POST /api/admin/stores 
@stores_admin_bp.route("", methods=["POST"])
@require_role("admin", "marketer")
def create_store():
    """Create a new store."""
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Bad Request", "message": "JSON body required"}), 400

    if not isinstance(data, dict):
        return jsonify({"error": "Bad Request", "message": "Request body must be a JSON object"}), 400

    errors = validate_create_store(data)
    if errors:
        return jsonify({"error": "Bad Request", "message": errors}), 400

    lat = data.get("latitude")
    lon = data.get("longitude")

    if lat is None or lon is None:
        address = f"{data.get('address_street')}, {data.get('address_city')}, {data.get('address_state')} {data.get('address_postal_code')}"
        coords = geocode_address(address)
        if not coords:
            return jsonify({
                "error": "Unprocessable Entity",
                "message": "Could not geocode the provided address. Please provide latitude and longitude directly.",
            }), 422
        lat, lon = coords

    store_id = data.get("store_id", "")
    if isinstance(store_id, str):
        store_id = store_id.strip()

    if store_id:
        from app.utils.validators import STORE_ID_RE
        if not STORE_ID_RE.match(store_id):
            return jsonify({"error": "Bad Request", "message": "store_id must match S#### format (e.g. S0001)"}), 400
        if db.session.get(Store, store_id):
            return jsonify({"error": "Conflict", "message": f"Store '{store_id}' already exists"}), 409
    else:
        store_id = _next_store_id()

    hours = data.get("hours", {})
    store = Store(
        store_id=store_id,
        name=data["name"].strip(),
        store_type=data["store_type"].strip().lower(),
        status=data.get("status", "active").strip().lower(),
        latitude=float(lat),
        longitude=float(lon),
        address_street=data["address_street"].strip(),
        address_city=data["address_city"].strip(),
        address_state=data["address_state"].strip().upper(),
        address_postal_code=data["address_postal_code"].strip(),
        address_country=data.get("address_country", "USA").strip(),
        phone=data.get("phone", "").strip() or None,
    )

    if isinstance(hours, dict):
        _apply_hours(store, hours)

    db.session.add(store)
    db.session.flush()

    services = data.get("services", [])
    if isinstance(services, list):
        _apply_services(store_id, services)

    db.session.commit()

    return jsonify({"message": "Store created", "store": store.to_dict()}), 201


# ─── GET /api/admin/stores 
@stores_admin_bp.route("", methods=["GET"])
@require_auth
def list_stores():
    """List all stores with pagination and optional filters."""
    try:
        page = max(1, int(request.args.get("page", 1)))
    except (ValueError, TypeError):
        return jsonify({"error": "Bad Request", "message": "page must be a positive integer"}), 400

    try:
        per_page = min(100, max(1, int(request.args.get("per_page", 20))))
    except (ValueError, TypeError):
        return jsonify({"error": "Bad Request", "message": "per_page must be a positive integer"}), 400

    query = Store.query

    status_filter = request.args.get("status", "").strip()
    if status_filter:
        if status_filter not in ALLOWED_STATUSES:
            return jsonify({"error": "Bad Request", "message": f"Invalid status '{status_filter}'. Choose from: {sorted(ALLOWED_STATUSES)}"}), 400
        query = query.filter(Store.status == status_filter)

    type_filter = request.args.get("store_type", "").strip()
    if type_filter:
        if type_filter not in ALLOWED_STORE_TYPES:
            return jsonify({"error": "Bad Request", "message": f"Invalid store_type '{type_filter}'. Choose from: {sorted(ALLOWED_STORE_TYPES)}"}), 400
        query = query.filter(Store.store_type == type_filter)

    search_q = request.args.get("search", "").strip()
    if search_q:
        like = f"%{search_q}%"
        query = query.filter(
            db.or_(Store.name.ilike(like), Store.address_city.ilike(like), Store.address_state.ilike(like))
        )

    pagination = query.order_by(Store.store_id).paginate(page=page, per_page=per_page, error_out=False)

    return jsonify({
        "stores": [s.to_dict() for s in pagination.items],
        "pagination": {
            "page": page,
            "per_page": per_page,
            "total": pagination.total,
            "pages": pagination.pages,
            "has_next": pagination.has_next,
            "has_prev": pagination.has_prev,
        },
    }), 200


# ─── GET /api/admin/stores/<store_id> 

@stores_admin_bp.route("/<store_id>", methods=["GET"])
@require_auth
def get_store(store_id):
    """Get a single store by ID."""
    store = db.session.get(Store, store_id.upper())
    if not store:
        return jsonify({"error": "Not Found", "message": f"Store '{store_id}' not found"}), 404
    return jsonify({"store": store.to_dict()}), 200


# ─── PATCH /api/admin/stores/<store_id>

@stores_admin_bp.route("/<store_id>", methods=["PATCH"])
@require_role("admin", "marketer")
def update_store(store_id):
    """Partially update a store. Allowed: name, phone, services, status, hours."""
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Bad Request", "message": "JSON body required"}), 400

    if not isinstance(data, dict):
        return jsonify({"error": "Bad Request", "message": "Request body must be a JSON object"}), 400

    if len(data) == 0:
        return jsonify({"error": "Bad Request", "message": "No fields provided to update"}), 400

    errors = validate_patch_store(data)
    if errors:
        return jsonify({"error": "Bad Request", "message": errors}), 400

    store = db.session.get(Store, store_id.upper())
    if not store:
        return jsonify({"error": "Not Found", "message": f"Store '{store_id}' not found"}), 404

    if "name" in data:
        store.name = str(data["name"]).strip()

    if "phone" in data:
        phone_val = data["phone"]
        if phone_val is None or phone_val == "":
            store.phone = None
        else:
            store.phone = str(phone_val).strip()

    if "status" in data:
        store.status = data["status"]

    if "services" in data:
        _apply_services(store_id.upper(), data["services"])

    if "hours" in data:
        _apply_hours(store, data["hours"])

    db.session.commit()

    return jsonify({"message": "Store updated", "store": store.to_dict()}), 200


# ─── DELETE /api/admin/stores/<store_id> 

@stores_admin_bp.route("/<store_id>", methods=["DELETE"])
@require_role("admin", "marketer")
def deactivate_store(store_id):
    """Soft delete a store (sets status to inactive)."""
    store = db.session.get(Store, store_id.upper())
    if not store:
        return jsonify({"error": "Not Found", "message": f"Store '{store_id}' not found"}), 404

    if store.status == "inactive":
        return jsonify({"error": "Conflict", "message": f"Store '{store_id}' is already inactive"}), 409

    store.status = "inactive"
    db.session.commit()

    return jsonify({"message": f"Store '{store_id}' has been deactivated", "store": store.to_dict()}), 200
