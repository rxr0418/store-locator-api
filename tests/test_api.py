"""
Test suite for Store Locator API.
Run: pytest tests/ -v --cov=app --cov-report=term-missing
"""
import pytest
import json
import io
from unittest.mock import patch
from app import create_app, db
from app.models import User, Role, Permission, Store, StoreService, RefreshToken
from app.middleware.auth import hash_password, hash_token, generate_access_token
from config import TestingConfig


# ─── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def app():
    app = create_app(TestingConfig)
    with app.app_context():
        db.create_all()
        _seed_test_data()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def app_ctx(app):
    with app.app_context():
        yield


def _seed_test_data():
    """Seed minimal data for tests."""
    from app.models import utcnow

    # Permissions
    perms = {}
    for name in ["stores:read", "stores:write", "stores:delete", "stores:import",
                 "users:read", "users:write", "users:delete"]:
        p = Permission(name=name, description=name)
        db.session.add(p)
        db.session.flush()
        perms[name] = p

    # Roles
    admin_role = Role(name="admin", description="Admin")
    admin_role.permissions = list(perms.values())
    db.session.add(admin_role)

    marketer_role = Role(name="marketer", description="Marketer")
    marketer_role.permissions = [perms[k] for k in ["stores:read", "stores:write", "stores:delete", "stores:import"]]
    db.session.add(marketer_role)

    viewer_role = Role(name="viewer", description="Viewer")
    viewer_role.permissions = [perms["stores:read"]]
    db.session.add(viewer_role)

    db.session.flush()

    # Users
    users_data = [
        ("U001", "admin@test.com", "AdminTest123!", admin_role.id),
        ("U002", "marketer@test.com", "MarketerTest123!", marketer_role.id),
        ("U003", "viewer@test.com", "ViewerTest123!", viewer_role.id),
    ]
    for uid, email, pwd, rid in users_data:
        u = User(user_id=uid, email=email, password_hash=hash_password(pwd),
                 role_id=rid, status="active", must_change_password=False)
        db.session.add(u)

    # Stores
    stores_data = [
        ("S0001", "Boston Downtown", "flagship", "active", 42.3555, -71.0602,
         "100 Cambridge St", "Boston", "MA", "02114", "617-555-0100",
         ["pharmacy", "pickup"], "08:00-22:00", "08:00-22:00", "08:00-22:00",
         "08:00-22:00", "08:00-23:00", "09:00-23:00", "10:00-20:00"),
        ("S0002", "Boston Back Bay", "regular", "active", 42.3505, -71.0837,
         "200 Boylston St", "Boston", "MA", "02116", "617-555-0200",
         ["optical", "returns"], "09:00-21:00", "09:00-21:00", "09:00-21:00",
         "09:00-21:00", "09:00-22:00", "10:00-22:00", "closed"),
        ("S0003", "Cambridge Store", "regular", "active", 42.3736, -71.1097,
         "50 Brattle St", "Cambridge", "MA", "02138", "617-555-0300",
         ["pickup", "pharmacy", "optical"], "08:00-20:00", "08:00-20:00",
         "08:00-20:00", "08:00-20:00", "08:00-21:00", "09:00-21:00", "11:00-18:00"),
        ("S0004", "Inactive Store", "outlet", "inactive", 42.3601, -71.0589,
         "999 Closed St", "Boston", "MA", "02101", "617-555-0000",
         [], "closed", "closed", "closed", "closed", "closed", "closed", "closed"),
        ("S0005", "NYC Store", "express", "active", 40.7128, -74.0060,
         "1 Broadway", "New York", "NY", "10004", "212-555-0100",
         ["pickup"], "07:00-23:00", "07:00-23:00", "07:00-23:00",
         "07:00-23:00", "07:00-23:00", "08:00-23:00", "09:00-22:00"),
    ]

    for row in stores_data:
        (sid, name, stype, status, lat, lon, street, city, state, postal, phone,
         services, hmon, htue, hwed, hthu, hfri, hsat, hsun) = row
        s = Store(
            store_id=sid, name=name, store_type=stype, status=status,
            latitude=lat, longitude=lon, address_street=street, address_city=city,
            address_state=state, address_postal_code=postal, address_country="USA",
            phone=phone, hours_mon=hmon, hours_tue=htue, hours_wed=hwed,
            hours_thu=hthu, hours_fri=hfri, hours_sat=hsat, hours_sun=hsun,
        )
        db.session.add(s)
        db.session.flush()
        for svc in services:
            db.session.add(StoreService(store_id=sid, service_name=svc))

    db.session.commit()


def _login(client, email="admin@test.com", password="AdminTest123!"):
    resp = client.post("/api/auth/login",
                       json={"email": email, "password": password})
    data = resp.get_json()
    return data.get("access_token"), data.get("refresh_token")


def _auth_headers(token):
    return {"Authorization": f"Bearer {token}"}


# ─── Unit Tests ────────────────────────────────────────────────────────────────

class TestGeoService:
    def test_bounding_box(self):
        from app.services.geo_service import calculate_bounding_box
        min_lat, max_lat, min_lon, max_lon = calculate_bounding_box(42.0, -71.0, 10.0)
        assert min_lat < 42.0 < max_lat
        assert min_lon < -71.0 < max_lon
        # ~10 miles ≈ 0.145 degrees lat
        assert abs((max_lat - min_lat) / 2 - 10 / 69.0) < 0.01

    def test_bounding_box_large_radius(self):
        from app.services.geo_service import calculate_bounding_box
        min_lat, max_lat, min_lon, max_lon = calculate_bounding_box(42.0, -71.0, 100.0)
        assert (max_lat - min_lat) > 2.0

    def test_parse_hours_valid(self):
        from app.services.geo_service import parse_hours
        assert parse_hours("08:00-22:00") == (480, 1320)
        assert parse_hours("00:00-23:59") == (0, 1439)

    def test_parse_hours_closed(self):
        from app.services.geo_service import parse_hours
        assert parse_hours("closed") is None
        assert parse_hours("CLOSED") is None
        assert parse_hours("") is None
        assert parse_hours(None) is None

    def test_parse_hours_invalid(self):
        from app.services.geo_service import parse_hours
        assert parse_hours("25:00-26:00") is None  # hours out of range still parsed as int
        assert parse_hours("not-hours") is None
        assert parse_hours("22:00-08:00") is None  # inverted times

    def test_validate_hours_string(self):
        from app.services.geo_service import validate_hours_string
        assert validate_hours_string("08:00-22:00") is True
        assert validate_hours_string("closed") is True
        assert validate_hours_string("CLOSED") is True
        assert validate_hours_string("bad") is False
        assert validate_hours_string("22:00-08:00") is False

    def test_haversine_distance(self):
        """Boston to Cambridge should be ~3 miles."""
        from geopy.distance import geodesic
        dist = geodesic((42.3555, -71.0602), (42.3736, -71.1097)).miles
        assert 2.0 < dist < 5.0


class TestPasswordUtils:
    def test_hash_and_verify(self):
        from app.middleware.auth import hash_password, verify_password
        hashed = hash_password("TestPassword123!")
        assert verify_password("TestPassword123!", hashed)
        assert not verify_password("WrongPassword", hashed)

    def test_different_hashes(self):
        from app.middleware.auth import hash_password
        h1 = hash_password("same")
        h2 = hash_password("same")
        assert h1 != h2  # bcrypt includes random salt

    def test_verify_empty_password(self):
        from app.middleware.auth import hash_password, verify_password
        hashed = hash_password("valid")
        assert not verify_password("", hashed)


class TestValidators:
    def test_validate_create_store_valid(self):
        from app.utils.validators import validate_create_store
        data = {
            "name": "Test Store", "store_type": "regular", "status": "active",
            "address_street": "123 Main St", "address_city": "Boston",
            "address_state": "MA", "address_postal_code": "02101",
            "latitude": 42.3, "longitude": -71.0,
        }
        assert validate_create_store(data) == []

    def test_validate_create_store_missing_name(self):
        from app.utils.validators import validate_create_store
        data = {
            "store_type": "regular", "address_street": "123 Main",
            "address_city": "Boston", "address_state": "MA", "address_postal_code": "02101"
        }
        errors = validate_create_store(data)
        assert any("name" in e for e in errors)

    def test_validate_create_store_invalid_type(self):
        from app.utils.validators import validate_create_store
        data = {
            "name": "Test", "store_type": "mega", "address_street": "123 Main",
            "address_city": "Boston", "address_state": "MA", "address_postal_code": "02101"
        }
        errors = validate_create_store(data)
        assert any("store_type" in e for e in errors)

    def test_validate_create_store_bad_lat(self):
        from app.utils.validators import validate_create_store
        data = {
            "name": "T", "store_type": "regular", "address_street": "123",
            "address_city": "Boston", "address_state": "MA", "address_postal_code": "02101",
            "latitude": 999, "longitude": -71,
        }
        errors = validate_create_store(data)
        assert any("latitude" in e for e in errors)

    def test_validate_patch_immutable_fields(self):
        from app.utils.validators import validate_patch_store
        errors = validate_patch_store({"latitude": 42.0})
        assert any("latitude" in e for e in errors)
        errors = validate_patch_store({"store_id": "S9999"})
        assert any("store_id" in e for e in errors)

    def test_validate_patch_valid_hours(self):
        from app.utils.validators import validate_patch_store
        data = {"hours": {"mon": "08:00-22:00", "sun": "closed"}}
        assert validate_patch_store(data) == []

    def test_validate_patch_invalid_hours(self):
        from app.utils.validators import validate_patch_store
        data = {"hours": {"mon": "22:00-08:00"}}
        errors = validate_patch_store(data)
        assert len(errors) > 0


# ─── Auth API Tests ────────────────────────────────────────────────────────────

class TestAuthLogin:
    def test_login_success(self, client):
        resp = client.post("/api/auth/login", json={"email": "admin@test.com", "password": "AdminTest123!"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["user"]["role"] == "admin"

    def test_login_wrong_password(self, client):
        resp = client.post("/api/auth/login", json={"email": "admin@test.com", "password": "wrong"})
        assert resp.status_code == 401

    def test_login_unknown_email(self, client):
        resp = client.post("/api/auth/login", json={"email": "nobody@test.com", "password": "anything"})
        assert resp.status_code == 401

    def test_login_missing_fields(self, client):
        resp = client.post("/api/auth/login", json={"email": "admin@test.com"})
        assert resp.status_code == 400

    def test_login_no_body(self, client):
        resp = client.post("/api/auth/login")
        assert resp.status_code == 400

    def test_login_invalid_email_format(self, client):
        resp = client.post("/api/auth/login", json={"email": "not-an-email", "password": "pass"})
        assert resp.status_code == 400


class TestAuthRefresh:
    def test_refresh_success(self, client):
        _, refresh_token = _login(client)
        resp = client.post("/api/auth/refresh", json={"refresh_token": refresh_token})
        assert resp.status_code == 200
        assert "access_token" in resp.get_json()

    def test_refresh_invalid_token(self, client):
        resp = client.post("/api/auth/refresh", json={"refresh_token": "invalid-token-value"})
        assert resp.status_code == 401

    def test_refresh_missing_token(self, client):
        resp = client.post("/api/auth/refresh", json={})
        assert resp.status_code == 400


class TestAuthLogout:
    def test_logout_success(self, client):
        access_token, refresh_token = _login(client)
        resp = client.post("/api/auth/logout",
                           json={"refresh_token": refresh_token},
                           headers=_auth_headers(access_token))
        assert resp.status_code == 200

    def test_logout_no_auth(self, client):
        resp = client.post("/api/auth/logout", json={"refresh_token": "something"})
        assert resp.status_code == 401

    def test_logout_bad_token_format(self, client):
        access_token, _ = _login(client)
        resp = client.post("/api/auth/logout",
                           json={"refresh_token": "nonexistent-token"},
                           headers=_auth_headers(access_token))
        assert resp.status_code == 404


class TestAuthMe:
    def test_me_success(self, client):
        access_token, _ = _login(client)
        resp = client.get("/api/auth/me", headers=_auth_headers(access_token))
        assert resp.status_code == 200
        assert resp.get_json()["user"]["email"] == "admin@test.com"

    def test_me_no_auth(self, client):
        resp = client.get("/api/auth/me")
        assert resp.status_code == 401

    def test_me_expired_token(self, client):
        resp = client.get("/api/auth/me", headers={"Authorization": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ1c2VyX2lkIjoiVTAwMSIsImV4cCI6MX0.invalid"})
        assert resp.status_code == 401


# ─── Store Search Tests ────────────────────────────────────────────────────────

class TestStoreSearch:
    def test_search_by_coordinates(self, client):
        resp = client.post("/api/stores/search", json={"latitude": 42.3601, "longitude": -71.0589, "radius_miles": 20})
        assert resp.status_code == 200
        data = resp.get_json()
        assert "results" in data
        assert "metadata" in data
        assert data["metadata"]["search_input_type"] == "coordinates"

    def test_search_results_sorted_by_distance(self, client):
        resp = client.post("/api/stores/search", json={"latitude": 42.3601, "longitude": -71.0589, "radius_miles": 50})
        assert resp.status_code == 200
        results = resp.get_json()["results"]
        if len(results) > 1:
            distances = [r["distance_miles"] for r in results]
            assert distances == sorted(distances)

    def test_search_excludes_inactive(self, client):
        resp = client.post("/api/stores/search", json={"latitude": 42.3601, "longitude": -71.0589, "radius_miles": 50})
        assert resp.status_code == 200
        for r in resp.get_json()["results"]:
            assert r["status"] == "active"

    def test_search_has_is_open_now(self, client):
        resp = client.post("/api/stores/search", json={"latitude": 42.3601, "longitude": -71.0589, "radius_miles": 20})
        assert resp.status_code == 200
        for r in resp.get_json()["results"]:
            assert "is_open_now" in r
            assert isinstance(r["is_open_now"], bool)

    def test_search_has_distance(self, client):
        resp = client.post("/api/stores/search", json={"latitude": 42.3601, "longitude": -71.0589})
        assert resp.status_code == 200
        for r in resp.get_json()["results"]:
            assert "distance_miles" in r

    def test_search_radius_filter(self, client):
        resp = client.post("/api/stores/search", json={"latitude": 42.3601, "longitude": -71.0589, "radius_miles": 0.1})
        assert resp.status_code == 200
        # Very small radius should return no or few stores
        results = resp.get_json()["results"]
        assert all(r["distance_miles"] <= 0.1 for r in results)

    def test_search_max_radius_capped(self, client):
        resp = client.post("/api/stores/search", json={"latitude": 42.3601, "longitude": -71.0589, "radius_miles": 9999})
        assert resp.status_code == 400  # exceeds max 100

    def test_search_filter_by_service(self, client):
        resp = client.post("/api/stores/search", json={
            "latitude": 42.3601, "longitude": -71.0589,
            "radius_miles": 50, "services": ["pharmacy"]
        })
        assert resp.status_code == 200
        for r in resp.get_json()["results"]:
            assert "pharmacy" in r["services"]

    def test_search_filter_by_multiple_services_and_logic(self, client):
        resp = client.post("/api/stores/search", json={
            "latitude": 42.3601, "longitude": -71.0589,
            "radius_miles": 50, "services": ["pharmacy", "pickup"]
        })
        assert resp.status_code == 200
        for r in resp.get_json()["results"]:
            assert "pharmacy" in r["services"]
            assert "pickup" in r["services"]

    def test_search_filter_by_store_type(self, client):
        resp = client.post("/api/stores/search", json={
            "latitude": 42.3601, "longitude": -71.0589,
            "radius_miles": 50, "store_types": ["flagship"]
        })
        assert resp.status_code == 200
        for r in resp.get_json()["results"]:
            assert r["store_type"] == "flagship"

    def test_search_filter_multiple_store_types_or_logic(self, client):
        resp = client.post("/api/stores/search", json={
            "latitude": 42.3601, "longitude": -71.0589,
            "radius_miles": 50, "store_types": ["flagship", "regular"]
        })
        assert resp.status_code == 200
        for r in resp.get_json()["results"]:
            assert r["store_type"] in ["flagship", "regular"]

    def test_search_no_input(self, client):
        resp = client.post("/api/stores/search", json={})
        assert resp.status_code == 400

    def test_search_invalid_lat(self, client):
        resp = client.post("/api/stores/search", json={"latitude": 999, "longitude": -71.0})
        assert resp.status_code == 400

    def test_search_invalid_lon(self, client):
        resp = client.post("/api/stores/search", json={"latitude": 42.0, "longitude": -999})
        assert resp.status_code == 400

    def test_search_invalid_service(self, client):
        resp = client.post("/api/stores/search", json={
            "latitude": 42.0, "longitude": -71.0, "services": ["fake_service"]
        })
        assert resp.status_code == 400

    def test_search_no_body(self, client):
        resp = client.post("/api/stores/search")
        assert resp.status_code == 400

    def test_search_by_postal_code_mocked(self, client):
        with patch("app.routes.stores_public.geocode_postal_code", return_value=(42.3601, -71.0589)):
            resp = client.post("/api/stores/search", json={"postal_code": "02101", "radius_miles": 20})
        assert resp.status_code == 200
        assert resp.get_json()["metadata"]["search_input_type"] == "postal_code"

    def test_search_by_address_mocked(self, client):
        with patch("app.routes.stores_public.geocode_address", return_value=(42.3601, -71.0589)):
            resp = client.post("/api/stores/search", json={"address": "100 Cambridge St, Boston, MA", "radius_miles": 20})
        assert resp.status_code == 200
        assert resp.get_json()["metadata"]["search_input_type"] == "address"

    def test_search_geocode_fail(self, client):
        with patch("app.routes.stores_public.geocode_postal_code", return_value=None):
            resp = client.post("/api/stores/search", json={"postal_code": "00000"})
        assert resp.status_code == 422

    def test_search_invalid_postal_code(self, client):
        resp = client.post("/api/stores/search", json={"postal_code": "ABCDE"})
        assert resp.status_code == 400

    def test_search_address_too_short(self, client):
        resp = client.post("/api/stores/search", json={"address": "hi"})
        assert resp.status_code == 400


# ─── Admin Store CRUD Tests ────────────────────────────────────────────────────

class TestAdminStores:
    def test_list_stores_admin(self, client):
        token, _ = _login(client)
        resp = client.get("/api/admin/stores", headers=_auth_headers(token))
        assert resp.status_code == 200
        data = resp.get_json()
        assert "stores" in data
        assert "pagination" in data

    def test_list_stores_viewer(self, client):
        token, _ = _login(client, "viewer@test.com", "ViewerTest123!")
        resp = client.get("/api/admin/stores", headers=_auth_headers(token))
        assert resp.status_code == 200  # viewer can read

    def test_list_stores_no_auth(self, client):
        resp = client.get("/api/admin/stores")
        assert resp.status_code == 401

    def test_list_stores_pagination(self, client):
        token, _ = _login(client)
        resp = client.get("/api/admin/stores?page=1&per_page=2", headers=_auth_headers(token))
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["stores"]) <= 2
        assert data["pagination"]["per_page"] == 2

    def test_list_stores_filter_status(self, client):
        token, _ = _login(client)
        resp = client.get("/api/admin/stores?status=inactive", headers=_auth_headers(token))
        assert resp.status_code == 200
        for s in resp.get_json()["stores"]:
            assert s["status"] == "inactive"

    def test_list_stores_invalid_status(self, client):
        token, _ = _login(client)
        resp = client.get("/api/admin/stores?status=badstatus", headers=_auth_headers(token))
        assert resp.status_code == 400

    def test_get_store_success(self, client):
        token, _ = _login(client)
        resp = client.get("/api/admin/stores/S0001", headers=_auth_headers(token))
        assert resp.status_code == 200
        assert resp.get_json()["store"]["store_id"] == "S0001"

    def test_get_store_not_found(self, client):
        token, _ = _login(client)
        resp = client.get("/api/admin/stores/S9999", headers=_auth_headers(token))
        assert resp.status_code == 404

    def test_create_store_admin(self, client):
        token, _ = _login(client)
        payload = {
            "name": "New Test Store", "store_type": "express", "status": "active",
            "latitude": 42.36, "longitude": -71.06,
            "address_street": "1 Test Ave", "address_city": "Boston",
            "address_state": "MA", "address_postal_code": "02101",
            "phone": "617-555-9999", "services": ["pickup"],
            "hours": {"mon": "09:00-21:00", "sun": "closed"},
        }
        resp = client.post("/api/admin/stores", json=payload, headers=_auth_headers(token))
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["store"]["name"] == "New Test Store"
        assert "pickup" in data["store"]["services"]

    def test_create_store_viewer_forbidden(self, client):
        token, _ = _login(client, "viewer@test.com", "ViewerTest123!")
        resp = client.post("/api/admin/stores", json={"name": "Hack"}, headers=_auth_headers(token))
        assert resp.status_code == 403

    def test_create_store_missing_required(self, client):
        token, _ = _login(client)
        resp = client.post("/api/admin/stores", json={"name": "Incomplete"}, headers=_auth_headers(token))
        assert resp.status_code == 400

    def test_create_store_invalid_type(self, client):
        token, _ = _login(client)
        payload = {
            "name": "Bad Type", "store_type": "mega",
            "address_street": "1 Main", "address_city": "Boston",
            "address_state": "MA", "address_postal_code": "02101",
        }
        resp = client.post("/api/admin/stores", json=payload, headers=_auth_headers(token))
        assert resp.status_code == 400

    def test_create_store_duplicate_id(self, client):
        token, _ = _login(client)
        payload = {
            "store_id": "S0001", "name": "Dupe", "store_type": "regular",
            "latitude": 42.0, "longitude": -71.0,
            "address_street": "1 Main", "address_city": "Boston",
            "address_state": "MA", "address_postal_code": "02101",
        }
        resp = client.post("/api/admin/stores", json=payload, headers=_auth_headers(token))
        assert resp.status_code == 409

    def test_patch_store_name(self, client):
        token, _ = _login(client)
        resp = client.patch("/api/admin/stores/S0001",
                            json={"name": "Updated Name"},
                            headers=_auth_headers(token))
        assert resp.status_code == 200
        assert resp.get_json()["store"]["name"] == "Updated Name"

    def test_patch_store_status(self, client):
        token, _ = _login(client)
        resp = client.patch("/api/admin/stores/S0002",
                            json={"status": "temporarily_closed"},
                            headers=_auth_headers(token))
        assert resp.status_code == 200
        assert resp.get_json()["store"]["status"] == "temporarily_closed"

    def test_patch_store_services(self, client):
        token, _ = _login(client)
        resp = client.patch("/api/admin/stores/S0003",
                            json={"services": ["pharmacy", "gift_wrapping"]},
                            headers=_auth_headers(token))
        assert resp.status_code == 200
        svcs = resp.get_json()["store"]["services"]
        assert set(svcs) == {"pharmacy", "gift_wrapping"}

    def test_patch_store_immutable_lat(self, client):
        token, _ = _login(client)
        resp = client.patch("/api/admin/stores/S0001",
                            json={"latitude": 99.0},
                            headers=_auth_headers(token))
        assert resp.status_code == 400

    def test_patch_store_immutable_store_id(self, client):
        token, _ = _login(client)
        resp = client.patch("/api/admin/stores/S0001",
                            json={"store_id": "S9999"},
                            headers=_auth_headers(token))
        assert resp.status_code == 400

    def test_patch_store_not_found(self, client):
        token, _ = _login(client)
        resp = client.patch("/api/admin/stores/S9999", json={"name": "Ghost"}, headers=_auth_headers(token))
        assert resp.status_code == 404

    def test_patch_store_viewer_forbidden(self, client):
        token, _ = _login(client, "viewer@test.com", "ViewerTest123!")
        resp = client.patch("/api/admin/stores/S0001", json={"name": "Hack"}, headers=_auth_headers(token))
        assert resp.status_code == 403

    def test_patch_hours_valid(self, client):
        token, _ = _login(client)
        resp = client.patch("/api/admin/stores/S0001",
                            json={"hours": {"mon": "07:00-21:00", "sun": "closed"}},
                            headers=_auth_headers(token))
        assert resp.status_code == 200

    def test_patch_hours_invalid(self, client):
        token, _ = _login(client)
        resp = client.patch("/api/admin/stores/S0001",
                            json={"hours": {"mon": "25:00-99:00"}},
                            headers=_auth_headers(token))
        assert resp.status_code == 400

    def test_delete_store_success(self, client):
        # Create a temp store to delete
        token, _ = _login(client)
        create_resp = client.post("/api/admin/stores", json={
            "name": "Temp Store", "store_type": "express",
            "latitude": 42.0, "longitude": -71.0,
            "address_street": "1 Temp", "address_city": "Boston",
            "address_state": "MA", "address_postal_code": "02101",
        }, headers=_auth_headers(token))
        new_id = create_resp.get_json()["store"]["store_id"]

        resp = client.delete(f"/api/admin/stores/{new_id}", headers=_auth_headers(token))
        assert resp.status_code == 200
        assert resp.get_json()["store"]["status"] == "inactive"

    def test_delete_store_already_inactive(self, client):
        token, _ = _login(client)
        resp = client.delete("/api/admin/stores/S0004", headers=_auth_headers(token))
        assert resp.status_code == 409

    def test_delete_store_not_found(self, client):
        token, _ = _login(client)
        resp = client.delete("/api/admin/stores/S9999", headers=_auth_headers(token))
        assert resp.status_code == 404


# ─── CSV Import Tests ──────────────────────────────────────────────────────────

class TestCSVImport:
    def _make_csv(self, rows: list, header=True) -> bytes:
        header_line = "store_id,name,store_type,status,latitude,longitude,address_street,address_city,address_state,address_postal_code,address_country,phone,services,hours_mon,hours_tue,hours_wed,hours_thu,hours_fri,hours_sat,hours_sun"
        lines = [header_line] if header else []
        lines.extend(rows)
        return "\n".join(lines).encode("utf-8")

    def test_import_success(self, client):
        token, _ = _login(client)
        csv_bytes = self._make_csv([
            "S0900,Import Test Store,regular,active,42.0,-71.0,100 Main,Boston,MA,02101,USA,617-555-1234,pickup|pharmacy,09:00-21:00,09:00-21:00,09:00-21:00,09:00-21:00,09:00-21:00,10:00-20:00,closed"
        ])
        resp = client.post("/api/admin/stores/import",
                           data={"file": (io.BytesIO(csv_bytes), "test.csv")},
                           headers=_auth_headers(token),
                           content_type="multipart/form-data")
        assert resp.status_code == 200
        report = resp.get_json()["report"]
        assert report["created"] >= 1
        assert report["failed"] == 0

    def test_import_upsert_update(self, client):
        token, _ = _login(client)
        # Import same store twice - second should be update
        csv_bytes = self._make_csv([
            "S0901,Original Name,regular,active,42.0,-71.0,100 Main,Boston,MA,02101,USA,617-555-1234,pickup,09:00-21:00,09:00-21:00,09:00-21:00,09:00-21:00,09:00-21:00,10:00-20:00,closed"
        ])
        client.post("/api/admin/stores/import",
                    data={"file": (io.BytesIO(csv_bytes), "test.csv")},
                    headers=_auth_headers(token),
                    content_type="multipart/form-data")

        csv_bytes2 = self._make_csv([
            "S0901,Updated Name,regular,active,42.0,-71.0,100 Main,Boston,MA,02101,USA,617-555-1234,pickup,09:00-21:00,09:00-21:00,09:00-21:00,09:00-21:00,09:00-21:00,10:00-20:00,closed"
        ])
        resp2 = client.post("/api/admin/stores/import",
                            data={"file": (io.BytesIO(csv_bytes2), "test.csv")},
                            headers=_auth_headers(token),
                            content_type="multipart/form-data")
        assert resp2.status_code == 200
        report = resp2.get_json()["report"]
        assert report["updated"] >= 1

    def test_import_no_file(self, client):
        token, _ = _login(client)
        resp = client.post("/api/admin/stores/import", headers=_auth_headers(token))
        assert resp.status_code == 400

    def test_import_wrong_extension(self, client):
        token, _ = _login(client)
        resp = client.post("/api/admin/stores/import",
                           data={"file": (io.BytesIO(b"data"), "test.txt")},
                           headers=_auth_headers(token),
                           content_type="multipart/form-data")
        assert resp.status_code == 400

    def test_import_missing_headers(self, client):
        token, _ = _login(client)
        csv_bytes = b"wrong,headers,here\nval1,val2,val3"
        resp = client.post("/api/admin/stores/import",
                           data={"file": (io.BytesIO(csv_bytes), "test.csv")},
                           headers=_auth_headers(token),
                           content_type="multipart/form-data")
        assert resp.status_code in (400, 422)

    def test_import_viewer_forbidden(self, client):
        token, _ = _login(client, "viewer@test.com", "ViewerTest123!")
        csv_bytes = self._make_csv([])
        resp = client.post("/api/admin/stores/import",
                           data={"file": (io.BytesIO(csv_bytes), "test.csv")},
                           headers=_auth_headers(token),
                           content_type="multipart/form-data")
        assert resp.status_code == 403

    def test_import_invalid_row(self, client):
        token, _ = _login(client)
        csv_bytes = self._make_csv([
            "INVALID_ID,Bad Store,wrongtype,active,999,-999,1 Main,Boston,MA,02101,USA,bad-phone,unknown_svc,nottime,x,x,x,x,x,x"
        ])
        resp = client.post("/api/admin/stores/import",
                           data={"file": (io.BytesIO(csv_bytes), "test.csv")},
                           headers=_auth_headers(token),
                           content_type="multipart/form-data")
        data = resp.get_json()
        assert data["report"]["failed"] >= 1
        assert len(data["report"]["errors"]) > 0


# ─── User Management Tests ────────────────────────────────────────────────────

class TestUserManagement:
    def test_list_users_admin(self, client):
        token, _ = _login(client)
        resp = client.get("/api/admin/users", headers=_auth_headers(token))
        assert resp.status_code == 200
        assert "users" in resp.get_json()

    def test_list_users_marketer_forbidden(self, client):
        token, _ = _login(client, "marketer@test.com", "MarketerTest123!")
        resp = client.get("/api/admin/users", headers=_auth_headers(token))
        assert resp.status_code == 403

    def test_create_user_success(self, client):
        token, _ = _login(client)
        resp = client.post("/api/admin/users",
                           json={"email": "newuser@test.com", "password": "NewPass123!", "role": "viewer"},
                           headers=_auth_headers(token))
        assert resp.status_code == 201
        assert resp.get_json()["user"]["email"] == "newuser@test.com"

    def test_create_user_duplicate_email(self, client):
        token, _ = _login(client)
        resp = client.post("/api/admin/users",
                           json={"email": "admin@test.com", "password": "Pass123!", "role": "viewer"},
                           headers=_auth_headers(token))
        assert resp.status_code == 409

    def test_create_user_invalid_role(self, client):
        token, _ = _login(client)
        resp = client.post("/api/admin/users",
                           json={"email": "bad@test.com", "password": "Pass123!", "role": "superadmin"},
                           headers=_auth_headers(token))
        assert resp.status_code == 400

    def test_create_user_weak_password(self, client):
        token, _ = _login(client)
        resp = client.post("/api/admin/users",
                           json={"email": "weak@test.com", "password": "abc", "role": "viewer"},
                           headers=_auth_headers(token))
        assert resp.status_code == 400

    def test_update_user_role(self, client):
        token, _ = _login(client)
        resp = client.put("/api/admin/users/U002",
                          json={"role": "viewer"},
                          headers=_auth_headers(token))
        assert resp.status_code == 200

    def test_update_user_invalid_role(self, client):
        token, _ = _login(client)
        resp = client.put("/api/admin/users/U002",
                          json={"role": "god"},
                          headers=_auth_headers(token))
        assert resp.status_code == 400

    def test_deactivate_user(self, client):
        # Create temp user then deactivate
        token, _ = _login(client)
        create = client.post("/api/admin/users",
                             json={"email": "todelete@test.com", "password": "Pass123!", "role": "viewer"},
                             headers=_auth_headers(token))
        uid = create.get_json()["user"]["user_id"]
        resp = client.delete(f"/api/admin/users/{uid}", headers=_auth_headers(token))
        assert resp.status_code == 200
        assert resp.get_json()["user"]["status"] == "inactive"

    def test_cannot_deactivate_self(self, client):
        token, _ = _login(client)
        resp = client.delete("/api/admin/users/U001", headers=_auth_headers(token))
        assert resp.status_code == 403

    def test_deactivate_user_already_inactive(self, client):
        token, _ = _login(client)
        # Deactivate once
        create = client.post("/api/admin/users",
                             json={"email": "alreadyinactive@test.com", "password": "Pass123!", "role": "viewer"},
                             headers=_auth_headers(token))
        uid = create.get_json()["user"]["user_id"]
        client.delete(f"/api/admin/users/{uid}", headers=_auth_headers(token))
        resp = client.delete(f"/api/admin/users/{uid}", headers=_auth_headers(token))
        assert resp.status_code == 409


# ─── Health endpoint ───────────────────────────────────────────────────────────

class TestHealth:
    def test_health_ok(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "ok"
