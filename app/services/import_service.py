"""
CSV import service: upsert stores from uploaded CSV file.
Validates structure, row data, geocodes if needed, uses a single transaction.
"""
import io
import csv
import logging
import re
from typing import Tuple
from app.models import Store, StoreService
from app.services.geo_service import geocode_address, validate_hours_string
from app import db

logger = logging.getLogger(__name__)

REQUIRED_HEADERS = [
    "store_id", "name", "store_type", "status",
    "latitude", "longitude",
    "address_street", "address_city", "address_state",
    "address_postal_code", "address_country", "phone", "services",
    "hours_mon", "hours_tue", "hours_wed", "hours_thu",
    "hours_fri", "hours_sat", "hours_sun",
]

VALID_STORE_TYPES = {"flagship", "regular", "outlet", "express"}
VALID_STATUSES = {"active", "inactive", "temporarily_closed"}
VALID_SERVICES = {
    "pharmacy", "pickup", "returns", "optical",
    "photo_printing", "gift_wrapping", "automotive", "garden_center",
}
PHONE_RE = re.compile(r"^\d{3}-\d{3}-\d{4}$")
STORE_ID_RE = re.compile(r"^S\d{4}$")
POSTAL_RE = re.compile(r"^\d{5}$")
STATE_RE = re.compile(r"^[A-Z]{2}$")
DAY_FIELDS = ["hours_mon", "hours_tue", "hours_wed", "hours_thu", "hours_fri", "hours_sat", "hours_sun"]


def _validate_row(row: dict, row_num: int) -> list:
    errors = []

    # store_id
    sid = row.get("store_id", "").strip()
    if not STORE_ID_RE.match(sid):
        errors.append(f"Row {row_num}: store_id '{sid}' must match S####")

    # name
    if not row.get("name", "").strip():
        errors.append(f"Row {row_num}: name is required")

    # store_type
    st = row.get("store_type", "").strip().lower()
    if st not in VALID_STORE_TYPES:
        errors.append(f"Row {row_num}: store_type '{st}' must be one of {VALID_STORE_TYPES}")

    # status
    status = row.get("status", "").strip().lower()
    if status not in VALID_STATUSES:
        errors.append(f"Row {row_num}: status '{status}' must be one of {VALID_STATUSES}")

    # lat / lon
    lat_str = row.get("latitude", "").strip()
    lon_str = row.get("longitude", "").strip()
    lat, lon = None, None
    try:
        lat = float(lat_str)
        if not (-90 <= lat <= 90):
            errors.append(f"Row {row_num}: latitude {lat} out of range [-90, 90]")
    except ValueError:
        errors.append(f"Row {row_num}: latitude '{lat_str}' is not a valid number")

    try:
        lon = float(lon_str)
        if not (-180 <= lon <= 180):
            errors.append(f"Row {row_num}: longitude {lon} out of range [-180, 180]")
    except ValueError:
        errors.append(f"Row {row_num}: longitude '{lon_str}' is not a valid number")

    # address fields
    for field in ["address_street", "address_city"]:
        if not row.get(field, "").strip():
            errors.append(f"Row {row_num}: {field} is required")

    state = row.get("address_state", "").strip()
    if not STATE_RE.match(state):
        errors.append(f"Row {row_num}: address_state '{state}' must be 2 uppercase letters")

    postal = row.get("address_postal_code", "").strip()
    if not POSTAL_RE.match(postal):
        errors.append(f"Row {row_num}: address_postal_code '{postal}' must be 5 digits")

    # phone (optional but must be valid if provided)
    phone = row.get("phone", "").strip()
    if phone and not PHONE_RE.match(phone):
        errors.append(f"Row {row_num}: phone '{phone}' must match XXX-XXX-XXXX")

    # services
    services_raw = row.get("services", "").strip()
    if services_raw:
        for svc in services_raw.split("|"):
            svc = svc.strip()
            if svc and svc not in VALID_SERVICES:
                errors.append(f"Row {row_num}: unknown service '{svc}'")

    # hours
    for day in DAY_FIELDS:
        h = row.get(day, "").strip()
        if h and not validate_hours_string(h):
            errors.append(f"Row {row_num}: {day} '{h}' must be HH:MM-HH:MM or 'closed'")

    return errors


def process_csv_import(file_bytes: bytes) -> dict:
    """
    Parse and import CSV. Returns a report dict with created/updated/failed counts.
    Uses a single database transaction (all-or-nothing on unrecoverable errors,
    but per-row errors are collected and reported without rolling back valid rows
    to allow partial import with clear reporting).
    """
    report = {
        "total_rows": 0,
        "created": 0,
        "updated": 0,
        "failed": 0,
        "errors": [],
    }

    try:
        text = file_bytes.decode("utf-8-sig")  # handle BOM
    except UnicodeDecodeError:
        try:
            text = file_bytes.decode("latin-1")
        except Exception:
            return {**report, "errors": ["File encoding error: must be UTF-8 or Latin-1"]}

    reader = csv.DictReader(io.StringIO(text))

    # Validate headers
    if not reader.fieldnames:
        return {**report, "errors": ["CSV file is empty or missing headers"]}

    actual_headers = [h.strip() for h in reader.fieldnames]
    missing = [h for h in REQUIRED_HEADERS if h not in actual_headers]
    if missing:
        return {**report, "errors": [f"Missing required CSV columns: {missing}"]}

    rows = list(reader)
    report["total_rows"] = len(rows)

    if not rows:
        return {**report, "errors": ["CSV contains no data rows"]}

    # Validate all rows first, collect errors per row
    row_errors = {}
    for i, row in enumerate(rows, start=2):  # row 1 = header
        errs = _validate_row(row, i)
        if errs:
            row_errors[i] = errs

    # Process valid rows within a transaction
    try:
        for i, row in enumerate(rows, start=2):
            if i in row_errors:
                report["failed"] += 1
                for err in row_errors[i]:
                    report["errors"].append(err)
                continue

            sid = row["store_id"].strip()
            lat = float(row["latitude"].strip())
            lon = float(row["longitude"].strip())

            # Auto-geocode if coords are 0,0 (missing)
            if lat == 0.0 and lon == 0.0:
                address = f"{row['address_street']}, {row['address_city']}, {row['address_state']} {row['address_postal_code']}"
                result = geocode_address(address)
                if result:
                    lat, lon = result

            services_list = []
            services_raw = row.get("services", "").strip()
            if services_raw:
                services_list = [s.strip() for s in services_raw.split("|") if s.strip()]

            existing = db.session.get(Store, sid)

            if existing:
                # UPDATE
                existing.name = row["name"].strip()
                existing.store_type = row["store_type"].strip().lower()
                existing.status = row["status"].strip().lower()
                existing.latitude = lat
                existing.longitude = lon
                existing.address_street = row["address_street"].strip()
                existing.address_city = row["address_city"].strip()
                existing.address_state = row["address_state"].strip().upper()
                existing.address_postal_code = row["address_postal_code"].strip()
                existing.address_country = row.get("address_country", "USA").strip()
                existing.phone = row.get("phone", "").strip() or None
                for day in DAY_FIELDS:
                    setattr(existing, day, row.get(day, "").strip() or None)

                # Rebuild services
                StoreService.query.filter_by(store_id=sid).delete()
                for svc in services_list:
                    if svc in VALID_SERVICES:
                        db.session.add(StoreService(store_id=sid, service_name=svc))

                report["updated"] += 1
            else:
                # CREATE
                store = Store(
                    store_id=sid,
                    name=row["name"].strip(),
                    store_type=row["store_type"].strip().lower(),
                    status=row["status"].strip().lower(),
                    latitude=lat,
                    longitude=lon,
                    address_street=row["address_street"].strip(),
                    address_city=row["address_city"].strip(),
                    address_state=row["address_state"].strip().upper(),
                    address_postal_code=row["address_postal_code"].strip(),
                    address_country=row.get("address_country", "USA").strip(),
                    phone=row.get("phone", "").strip() or None,
                )
                for day in DAY_FIELDS:
                    setattr(store, day, row.get(day, "").strip() or None)

                db.session.add(store)
                db.session.flush()  # get store in session before adding services

                for svc in services_list:
                    if svc in VALID_SERVICES:
                        db.session.add(StoreService(store_id=sid, service_name=svc))

                report["created"] += 1

        db.session.commit()

    except Exception as e:
        db.session.rollback()
        logger.exception("CSV import transaction failed")
        report["errors"].append(f"Database error: {str(e)}")
        # Recalculate counts
        report["failed"] = report["total_rows"]
        report["created"] = 0
        report["updated"] = 0

    return report
