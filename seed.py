"""
Database initialization and seeding script.
Run: python seed.py
"""
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from app import create_app, db
from app.models import User, Role, Permission, Store, StoreService, RefreshToken
from app.middleware.auth import hash_password
import csv

PERMISSIONS = [
    ("stores:read", "Read store data"),
    ("stores:write", "Create/update stores"),
    ("stores:delete", "Deactivate stores"),
    ("stores:import", "Batch import stores"),
    ("users:read", "Read user data"),
    ("users:write", "Create/update users"),
    ("users:delete", "Deactivate users"),
]

ROLE_PERMISSIONS = {
    "admin": [p[0] for p in PERMISSIONS],  # all
    "marketer": ["stores:read", "stores:write", "stores:delete", "stores:import"],
    "viewer": ["stores:read"],
}

SEED_USERS = [
    {
        "user_id": "U001",
        "email": "admin@test.com",
        "password": "AdminTest123!",
        "first_name": "Admin",
        "last_name": "User",
        "role": "admin",
        "must_change_password": False,
    },
    {
        "user_id": "U002",
        "email": "marketer@test.com",
        "password": "MarketerTest123!",
        "first_name": "Marketing",
        "last_name": "User",
        "role": "marketer",
        "must_change_password": False,
    },
    {
        "user_id": "U003",
        "email": "viewer@test.com",
        "password": "ViewerTest123!",
        "first_name": "Viewer",
        "last_name": "User",
        "role": "viewer",
        "must_change_password": False,
    },
]


def seed_permissions_and_roles(app):
    with app.app_context():
        print("Seeding permissions...")
        perm_map = {}
        for name, desc in PERMISSIONS:
            p = Permission.query.filter_by(name=name).first()
            if not p:
                p = Permission(name=name, description=desc)
                db.session.add(p)
                db.session.flush()
            perm_map[name] = p

        print("Seeding roles...")
        for role_name, perm_names in ROLE_PERMISSIONS.items():
            role = Role.query.filter_by(name=role_name).first()
            if not role:
                role = Role(name=role_name, description=f"{role_name.title()} role")
                db.session.add(role)
                db.session.flush()
            role.permissions = [perm_map[p] for p in perm_names]

        db.session.commit()
        print("Roles and permissions seeded.")


def seed_users(app):
    with app.app_context():
        print("Seeding users...")
        for u in SEED_USERS:
            existing = User.query.filter_by(email=u["email"]).first()
            if existing:
                print(f"  User {u['email']} already exists, skipping")
                continue
            role = Role.query.filter_by(name=u["role"]).first()
            if not role:
                print(f"  Role '{u['role']}' not found, skipping {u['email']}")
                continue
            user = User(
                user_id=u["user_id"],
                email=u["email"],
                password_hash=hash_password(u["password"]),
                first_name=u["first_name"],
                last_name=u["last_name"],
                role_id=role.id,
                status="active",
                must_change_password=u.get("must_change_password", True),
            )
            db.session.add(user)
            print(f"  Created user: {u['email']} (role: {u['role']})")
        db.session.commit()
        print("Users seeded.")


def seed_stores_from_csv(app, csv_path: str):
    """Load stores from the 50-store seed file."""
    VALID_SERVICES = {
        "pharmacy", "pickup", "returns", "optical",
        "photo_printing", "gift_wrapping", "automotive", "garden_center",
    }
    DAY_FIELDS = ["hours_mon", "hours_tue", "hours_wed", "hours_thu", "hours_fri", "hours_sat", "hours_sun"]

    with app.app_context():
        print(f"Seeding stores from {csv_path}...")
        created = 0
        skipped = 0

        with open(csv_path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                sid = row["store_id"].strip()
                if db.session.get(Store, sid):
                    skipped += 1
                    continue

                try:
                    lat = float(row["latitude"].strip())
                    lon = float(row["longitude"].strip())
                except ValueError:
                    print(f"  Skipping {sid}: invalid coordinates")
                    continue

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
                db.session.flush()

                services_raw = row.get("services", "").strip()
                if services_raw:
                    for svc in services_raw.split("|"):
                        svc = svc.strip()
                        if svc in VALID_SERVICES:
                            db.session.add(StoreService(store_id=sid, service_name=svc))

                created += 1

        db.session.commit()
        print(f"  Created {created} stores, skipped {skipped} existing.")


def init_db(app):
    with app.app_context():
        print("Creating database tables...")
        db.create_all()
        print("Tables created.")


if __name__ == "__main__":
    app = create_app()

    init_db(app)
    seed_permissions_and_roles(app)
    seed_users(app)

    # Seed from 50-store CSV
    csv_path = os.path.join(os.path.dirname(__file__), "stores_50.csv")
    if os.path.exists(csv_path):
        seed_stores_from_csv(app, csv_path)
    else:
        print(f"Warning: {csv_path} not found. Place stores_50.csv in project root to seed stores.")

    print("\nDatabase initialized successfully!")
    print("\nTest credentials:")
    for u in SEED_USERS:
        print(f"  {u['role']:10s} → {u['email']}  /  {u['password']}")
