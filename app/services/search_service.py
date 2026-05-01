"""
Store search service: bounding box pre-filter + Haversine exact distance.
"""
import logging
from typing import List, Optional, Tuple
from geopy.distance import geodesic
from sqlalchemy import and_
from app.models import Store, StoreService
from app.services.geo_service import calculate_bounding_box, is_store_open_now
from app import db

logger = logging.getLogger(__name__)

VALID_SERVICES = {
    "pharmacy", "pickup", "returns", "optical",
    "photo_printing", "gift_wrapping", "automotive", "garden_center",
}
VALID_STORE_TYPES = {"flagship", "regular", "outlet", "express"}


def search_stores(
    lat: float,
    lon: float,
    radius_miles: float = 10.0,
    services: Optional[List[str]] = None,
    store_types: Optional[List[str]] = None,
    open_now: bool = False,
    limit: int = 50,
) -> Tuple[List[dict], dict]:
    """
    Returns (results_list, metadata_dict).
    results_list: list of store dicts with distance_miles and is_open_now.
    """
    radius_miles = min(float(radius_miles), 100.0)
    radius_miles = max(float(radius_miles), 0.1)

    min_lat, max_lat, min_lon, max_lon = calculate_bounding_box(lat, lon, radius_miles)

    query = Store.query.filter(
        Store.status == "active",
        Store.latitude.between(min_lat, max_lat),
        Store.longitude.between(min_lon, max_lon),
    )

    # Filter by store types (OR logic)
    if store_types:
        valid_types = [t for t in store_types if t in VALID_STORE_TYPES]
        if valid_types:
            query = query.filter(Store.store_type.in_(valid_types))

    # Filter by services (AND logic)
    if services:
        valid_svcs = [s for s in services if s in VALID_SERVICES]
        for svc in valid_svcs:
            subq = db.session.query(StoreService.store_id).filter(
                StoreService.service_name == svc
            ).subquery()
            query = query.filter(Store.store_id.in_(subq))

    candidate_stores = query.all()


    results = []
    for store in candidate_stores:
        dist = geodesic(
            (lat, lon),
            (float(store.latitude), float(store.longitude)),
        ).miles

        if dist <= radius_miles:
            store_open = is_store_open_now(store)

            if open_now and not store_open:
                continue

            d = store.to_dict(distance=dist)
            d["is_open_now"] = store_open
            results.append(d)


    results.sort(key=lambda x: x["distance_miles"])
    results = results[:limit]

    metadata = {
        "search_location": {"latitude": lat, "longitude": lon},
        "radius_miles": radius_miles,
        "filters_applied": {
            "services": services or [],
            "store_types": store_types or [],
            "open_now": open_now,
        },
        "total_results": len(results),
    }

    return results, metadata
