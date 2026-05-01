"""
Production initialization script.
Run ONCE after first deployment to set up DB and seed data.

Usage:
  python init_production.py
  python init_production.py --skip-stores   # skip CSV import (faster)
"""
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from app import create_app, db
from app.models import User, Role, Permission, Store, StoreService, RefreshToken
from app.middleware.auth import hash_password
import csv

PERMISSIONS = [
    ("stores:read",   "Read store data"),
    ("stores:write",  "Create/update stores"),
    ("stores:delete", "Deactivate stores"),
    ("stores:import", "Batch import stores"),
    ("users:read",    "Read user data"),
    ("users:write",   "Create/update users"),
    ("users:delete",  "Deactivate users"),
]

ROLE_PERMISSIONS = {
    "admin":    [p[0] for p in PERMISSIONS],
    "marketer": ["stores:read", "stores:write", "stores:delete", "stores:import"],
    "viewer":   ["stores:read"],
}

SEED_USERS = [
    {"user_id": "U001", "email": "admin@test.com",    "password": "AdminTest123!",    "first_name": "Admin",     "last_name": "User",    "role": "admin",    "must_change_password": False},
    {"user_id": "U002", "email": "marketer@test.com", "password": "MarketerTest123!", "first_name": "Marketing", "last_name": "User",    "role": "marketer", "must_change_password": False},
    {"user_id": "U003", "email": "viewer@test.com",   "password": "ViewerTest123!",   "first_name": "Viewer",    "last_name": "User",    "role": "viewer",   "must_change_password": False},
]


def run(skip_stores=False):
    app = create_app()

    with app.app_context():
        print("=== Store Locator — Production Init ===\n")

        # 1. Create tables
        print("1. Creating database tables...")
        db.create_all()
        print("   ✓ Tables created\n")

        # 2. Permissions
        print("2. Seeding permissions...")
        perm_map = {}
        for name, desc in PERMISSIONS:
            p = Permission.query.filter_by(name=name).first()
            if not p:
                p = Permission(name=name, description=desc)
                db.session.add(p)
                db.session.flush()
            perm_map[name] = p
        db.session.commit()
        print(f"   ✓ {len(PERMISSIONS)} permissions ready\n")

        # 3. Roles
        print("3. Seeding roles...")
        for role_name, perm_names in ROLE_PERMISSIONS.items():
            role = Role.query.filter_by(name=role_name).first()
            if not role:
                role = Role(name=role_name, description=f"{role_name.title()} role")
                db.session.add(role)
                db.session.flush()
            role.permissions = [perm_map[p] for p in perm_names]
        db.session.commit()
        print("   ✓ admin, marketer, viewer roles ready\n")

        # 4. Users
        print("4. Seeding users...")
        for u in SEED_USERS:
            if User.query.filter_by(email=u["email"]).first():
                print(f"   - {u['email']} already exists, skipping")
                continue
            role = Role.query.filter_by(name=u["role"]).first()
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
            print(f"   + Created {u['email']} ({u['role']})")
        db.session.commit()
        print()

        # 5. Stores
        if skip_stores:
            print("5. Skipping store CSV import (--skip-stores flag set)\n")
        else:
            VALID_SERVICES = {
                "pharmacy", "pickup", "returns", "optical",
                "photo_printing", "gift_wrapping", "automotive", "garden_center",
            }
            DAY_FIELDS = ["hours_mon","hours_tue","hours_wed","hours_thu","hours_fri","hours_sat","hours_sun"]

            # Try 1000-store file first, fall back to 50
            for csv_name in ["stores_1000.csv", "stores_50.csv"]:
                csv_path = os.path.join(os.path.dirname(__file__), csv_name)
                if os.path.exists(csv_path):
                    print(f"5. Importing stores from {csv_name}...")
                    created = updated = skipped = 0
                    with open(csv_path, newline="", encoding="utf-8-sig") as f:
                        for row in csv.DictReader(f):
                            sid = row["store_id"].strip()
                            try:
                                lat = float(row["latitude"].strip())
                                lon = float(row["longitude"].strip())
                            except ValueError:
                                skipped += 1
                                continue

                            existing = db.session.get(Store, sid)
                            if existing:
                                skipped += 1
                                continue

                            store = Store(
                                store_id=sid, name=row["name"].strip(),
                                store_type=row["store_type"].strip().lower(),
                                status=row["status"].strip().lower(),
                                latitude=lat, longitude=lon,
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

                            for svc in row.get("services", "").split("|"):
                                svc = svc.strip()
                                if svc in VALID_SERVICES:
                                    db.session.add(StoreService(store_id=sid, service_name=svc))
                            created += 1

                            if created % 100 == 0:
                                db.session.commit()
                                print(f"   ... {created} stores imported")

                    db.session.commit()
                    print(f"   ✓ Created {created}, skipped {skipped} existing\n")
                    break
            else:
                print("5. No CSV file found — skipping store import\n")

        print("=== Initialization complete! ===\n")
        print("Test credentials:")
        for u in SEED_USERS:
            print(f"  {u['role']:10s}  {u['email']}  /  {u['password']}")


if __name__ == "__main__":
    skip_stores = "--skip-stores" in sys.argv
    run(skip_stores=skip_stores)
