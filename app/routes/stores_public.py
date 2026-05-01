from flask import Blueprint, request, jsonify, current_app
from app import limiter
from app.services.search_service import search_stores, VALID_SERVICES, VALID_STORE_TYPES
from app.services.geo_service import geocode_address, geocode_postal_code

stores_public_bp = Blueprint("stores_public", __name__)


def _parse_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).lower() in ("true", "1", "yes")


def _get_search_params(data: dict) -> tuple:
    """Extract and validate search query parameters from request body."""
    errors = []

    radius = data.get("radius_miles", current_app.config["DEFAULT_SEARCH_RADIUS_MILES"])
    try:
        radius = float(radius)
        if radius <= 0:
            errors.append("radius_miles must be positive")
        if radius > current_app.config["MAX_SEARCH_RADIUS_MILES"]:
            errors.append(f"radius_miles cannot exceed {current_app.config['MAX_SEARCH_RADIUS_MILES']}")
    except (TypeError, ValueError):
        errors.append("radius_miles must be a number")
        radius = 10.0

    services = data.get("services", [])
    if isinstance(services, str):
        services = [s.strip() for s in services.split(",") if s.strip()]
    if not isinstance(services, list):
        services = []
    invalid_svcs = [s for s in services if s not in VALID_SERVICES]
    if invalid_svcs:
        errors.append(f"Unknown services: {invalid_svcs}. Valid: {sorted(VALID_SERVICES)}")
        services = [s for s in services if s in VALID_SERVICES]

    store_types = data.get("store_types", [])
    if isinstance(store_types, str):
        store_types = [t.strip() for t in store_types.split(",") if t.strip()]
    if not isinstance(store_types, list):
        store_types = []
    invalid_types = [t for t in store_types if t not in VALID_STORE_TYPES]
    if invalid_types:
        errors.append(f"Unknown store types: {invalid_types}. Valid: {sorted(VALID_STORE_TYPES)}")
        store_types = [t for t in store_types if t in VALID_STORE_TYPES]

    open_now = _parse_bool(data.get("open_now", False))

    return radius, services, store_types, open_now, errors


@stores_public_bp.route("/search", methods=["POST"])
@limiter.limit("100 per hour")
@limiter.limit("10 per minute")
def search():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Bad Request", "message": "JSON body required"}), 400

    lat = data.get("latitude")
    lon = data.get("longitude")
    address = data.get("address", "").strip()
    postal_code = data.get("postal_code", "").strip()
    search_input_type = None
    geocode_query = None

    if lat is not None and lon is not None:
        try:
            lat = float(lat)
            lon = float(lon)
        except (TypeError, ValueError):
            return jsonify({"error": "Bad Request", "message": "latitude and longitude must be numbers"}), 400
        if not (-90 <= lat <= 90):
            return jsonify({"error": "Bad Request", "message": "latitude must be between -90 and 90"}), 400
        if not (-180 <= lon <= 180):
            return jsonify({"error": "Bad Request", "message": "longitude must be between -180 and 180"}), 400
        search_input_type = "coordinates"

    elif postal_code:
        if not postal_code.isdigit() or len(postal_code) != 5:
            return jsonify({"error": "Bad Request", "message": "postal_code must be a 5-digit ZIP code"}), 400
        coords = geocode_postal_code(postal_code)
        if not coords:
            return jsonify({
                "error": "Unprocessable Entity",
                "message": f"Could not geocode postal code '{postal_code}'. Please try a different postal code or use coordinates directly.",
            }), 422
        lat, lon = coords
        search_input_type = "postal_code"
        geocode_query = postal_code

    elif address:
        if len(address) < 5:
            return jsonify({"error": "Bad Request", "message": "address is too short"}), 400
        coords = geocode_address(address)
        if not coords:
            return jsonify({
                "error": "Unprocessable Entity",
                "message": f"Could not geocode address '{address}'. Please check the address or use coordinates directly.",
            }), 422
        lat, lon = coords
        search_input_type = "address"
        geocode_query = address

    else:
        return jsonify({
            "error": "Bad Request",
            "message": "Provide one of: (latitude + longitude), postal_code, or address",
        }), 400

    radius, services, store_types, open_now, param_errors = _get_search_params(data)

    if param_errors:
        return jsonify({"error": "Bad Request", "message": "; ".join(param_errors)}), 400

    results, metadata = search_stores(
        lat=lat,
        lon=lon,
        radius_miles=radius,
        services=services or None,
        store_types=store_types or None,
        open_now=open_now,
    )

    metadata["search_input_type"] = search_input_type
    if geocode_query:
        metadata["geocoded_query"] = geocode_query

    return jsonify({
        "results": results,
        "metadata": metadata,
    }), 200
