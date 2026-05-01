"""
Input validation helpers shared across routes.
"""
import re


PHONE_RE = re.compile(r"^\d{3}-\d{3}-\d{4}$")
STORE_ID_RE = re.compile(r"^S\d{4}$")
POSTAL_RE = re.compile(r"^\d{5}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
HOURS_RE = re.compile(r"^(\d{2}):(\d{2})-(\d{2}):(\d{2})$")

VALID_STORE_TYPES = {"flagship", "regular", "outlet", "express"}
VALID_STATUSES = {"active", "inactive", "temporarily_closed"}
VALID_SERVICES = {
    "pharmacy", "pickup", "returns", "optical",
    "photo_printing", "gift_wrapping", "automotive", "garden_center",
}
PATCHABLE_FIELDS = {"name", "phone", "services", "status", "hours"}
DAY_KEYS = ["hours_mon", "hours_tue", "hours_wed", "hours_thu", "hours_fri", "hours_sat", "hours_sun"]


def validate_hours_value(v: str) -> bool:
    if v is None:
        return True
    s = str(v).strip().lower()
    if s == "closed":
        return True
    m = HOURS_RE.match(s)
    if not m:
        return False
    open_m = int(m.group(1)) * 60 + int(m.group(2))
    close_m = int(m.group(3)) * 60 + int(m.group(4))
    return open_m < close_m


def validate_create_store(data: dict) -> list:
    errors = []
    required = ["name", "store_type", "address_street", "address_city", "address_state", "address_postal_code"]
    for field in required:
        if not data.get(field, "").strip():
            errors.append(f"'{field}' is required")

    st = data.get("store_type", "")
    if st and st not in VALID_STORE_TYPES:
        errors.append(f"'store_type' must be one of {sorted(VALID_STORE_TYPES)}")

    status = data.get("status", "active")
    if status and status not in VALID_STATUSES:
        errors.append(f"'status' must be one of {sorted(VALID_STATUSES)}")

    postal = data.get("address_postal_code", "")
    if postal and not POSTAL_RE.match(postal):
        errors.append("'address_postal_code' must be 5 digits")

    state = data.get("address_state", "")
    if state and (len(state) != 2 or not state.isalpha()):
        errors.append("'address_state' must be 2 letters")

    phone = data.get("phone", "")
    if phone and not PHONE_RE.match(phone):
        errors.append("'phone' must be in XXX-XXX-XXXX format")

    if "latitude" in data and "longitude" in data:
        try:
            lat = float(data["latitude"])
            if not (-90 <= lat <= 90):
                errors.append("'latitude' must be between -90 and 90")
        except (TypeError, ValueError):
            errors.append("'latitude' must be a number")
        try:
            lon = float(data["longitude"])
            if not (-180 <= lon <= 180):
                errors.append("'longitude' must be between -180 and 180")
        except (TypeError, ValueError):
            errors.append("'longitude' must be a number")

    services = data.get("services", [])
    if isinstance(services, list):
        for svc in services:
            if svc not in VALID_SERVICES:
                errors.append(f"Unknown service '{svc}'. Valid: {sorted(VALID_SERVICES)}")
    elif services:
        errors.append("'services' must be a list")

    # hours validation
    hours = data.get("hours", {})
    if isinstance(hours, dict):
        for day, val in hours.items():
            if not validate_hours_value(val):
                errors.append(f"'hours.{day}' value '{val}' is invalid. Use HH:MM-HH:MM or 'closed'")

    return errors


def validate_patch_store(data: dict) -> list:
    errors = []
    immutable = {"store_id", "latitude", "longitude", "address_street", "address_city",
                 "address_state", "address_postal_code", "address_country"}

    for field in immutable:
        if field in data:
            errors.append(f"Field '{field}' cannot be updated via PATCH. Use dedicated endpoint.")

    name = data.get("name", "")
    if "name" in data and not str(name).strip():
        errors.append("'name' cannot be empty")

    phone = data.get("phone")
    if phone is not None and phone != "" and not PHONE_RE.match(str(phone)):
        errors.append("'phone' must be in XXX-XXX-XXXX format")

    status = data.get("status")
    if status is not None and status not in VALID_STATUSES:
        errors.append(f"'status' must be one of {sorted(VALID_STATUSES)}")

    services = data.get("services")
    if services is not None:
        if not isinstance(services, list):
            errors.append("'services' must be a list")
        else:
            for svc in services:
                if svc not in VALID_SERVICES:
                    errors.append(f"Unknown service '{svc}'")

    hours = data.get("hours")
    if hours is not None:
        if not isinstance(hours, dict):
            errors.append("'hours' must be an object with day keys")
        else:
            valid_days = {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}
            for day, val in hours.items():
                if day not in valid_days:
                    errors.append(f"Unknown day key '{day}' in hours")
                elif not validate_hours_value(val):
                    errors.append(f"'hours.{day}' value '{val}' is invalid")

    return errors


def validate_create_user(data: dict) -> list:
    errors = []

    email = data.get("email", "")
    if not email or not EMAIL_RE.match(email):
        errors.append("Valid 'email' is required")

    password = data.get("password", "")
    if not password or len(password) < 8:
        errors.append("'password' must be at least 8 characters")

    role = data.get("role", "")
    if role not in {"admin", "marketer", "viewer"}:
        errors.append("'role' must be one of: admin, marketer, viewer")

    return errors
