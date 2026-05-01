from datetime import datetime, timezone
from app import db
import uuid


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


store_services = db.Table(
    "store_services",
    db.Column("store_id", db.String(10), db.ForeignKey("stores.store_id"), primary_key=True),
    db.Column("service_name", db.String(50), primary_key=True),
)


class Store(db.Model):
    __tablename__ = "stores"

    store_id = db.Column(db.String(10), primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    store_type = db.Column(
        db.Enum("flagship", "regular", "outlet", "express", name="store_type_enum"),
        nullable=False,
    )
    status = db.Column(
        db.Enum("active", "inactive", "temporarily_closed", name="store_status_enum"),
        nullable=False,
        default="active",
    )

    latitude = db.Column(db.Numeric(10, 7), nullable=False)
    longitude = db.Column(db.Numeric(11, 7), nullable=False)

    address_street = db.Column(db.String(300), nullable=False)
    address_city = db.Column(db.String(100), nullable=False)
    address_state = db.Column(db.String(2), nullable=False)
    address_postal_code = db.Column(db.String(10), nullable=False)
    address_country = db.Column(db.String(3), nullable=False, default="USA")

    phone = db.Column(db.String(20), nullable=True)

    hours_mon = db.Column(db.String(20), nullable=True)
    hours_tue = db.Column(db.String(20), nullable=True)
    hours_wed = db.Column(db.String(20), nullable=True)
    hours_thu = db.Column(db.String(20), nullable=True)
    hours_fri = db.Column(db.String(20), nullable=True)
    hours_sat = db.Column(db.String(20), nullable=True)
    hours_sun = db.Column(db.String(20), nullable=True)

    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

 
    services = db.relationship(
        "StoreService",
        back_populates="store",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    __table_args__ = (
        db.Index("idx_stores_lat_lon", "latitude", "longitude"),
        db.Index("idx_stores_status", "status"),
        db.Index("idx_stores_type", "store_type"),
        db.Index("idx_stores_postal", "address_postal_code"),
    )

    def get_services_list(self):
        return [s.service_name for s in self.services]

    def get_hours_dict(self):
        return {
            "mon": self.hours_mon,
            "tue": self.hours_tue,
            "wed": self.hours_wed,
            "thu": self.hours_thu,
            "fri": self.hours_fri,
            "sat": self.hours_sat,
            "sun": self.hours_sun,
        }

    def to_dict(self, distance=None):
        data = {
            "store_id": self.store_id,
            "name": self.name,
            "store_type": self.store_type,
            "status": self.status,
            "latitude": float(self.latitude),
            "longitude": float(self.longitude),
            "address": {
                "street": self.address_street,
                "city": self.address_city,
                "state": self.address_state,
                "postal_code": self.address_postal_code,
                "country": self.address_country,
            },
            "phone": self.phone,
            "services": self.get_services_list(),
            "hours": self.get_hours_dict(),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
        if distance is not None:
            data["distance_miles"] = round(distance, 2)
        return data


class StoreService(db.Model):
    __tablename__ = "store_service_items"

    id = db.Column(db.Integer, primary_key=True)
    store_id = db.Column(db.String(10), db.ForeignKey("stores.store_id"), nullable=False)
    service_name = db.Column(db.String(50), nullable=False)

    store = db.relationship("Store", back_populates="services")

    __table_args__ = (
        db.UniqueConstraint("store_id", "service_name", name="uq_store_service"),
        db.Index("idx_store_service", "store_id", "service_name"),
    )


# ─── Role / Permission / User 

role_permissions = db.Table(
    "role_permissions",
    db.Column("role_id", db.Integer, db.ForeignKey("roles.id"), primary_key=True),
    db.Column("permission_id", db.Integer, db.ForeignKey("permissions.id"), primary_key=True),
)


class Role(db.Model):
    __tablename__ = "roles"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(
        db.Enum("admin", "marketer", "viewer", name="role_name_enum"),
        unique=True,
        nullable=False,
    )
    description = db.Column(db.String(200))

    permissions = db.relationship("Permission", secondary=role_permissions, backref="roles", lazy="selectin")

    def get_permission_names(self):
        return [p.name for p in self.permissions]


class Permission(db.Model):
    __tablename__ = "permissions"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    description = db.Column(db.String(200))


class User(db.Model):
    __tablename__ = "users"

    user_id = db.Column(db.String(10), primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    first_name = db.Column(db.String(100))
    last_name = db.Column(db.String(100))
    role_id = db.Column(db.Integer, db.ForeignKey("roles.id"), nullable=False)
    status = db.Column(
        db.Enum("active", "inactive", name="user_status_enum"),
        default="active",
        nullable=False,
    )
    must_change_password = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

    role = db.relationship("Role", backref="users", lazy="selectin")
    refresh_tokens = db.relationship("RefreshToken", back_populates="user", cascade="all, delete-orphan")

    __table_args__ = (db.Index("idx_users_email", "email"),)

    def to_dict(self):
        return {
            "user_id": self.user_id,
            "email": self.email,
            "first_name": self.first_name,
            "last_name": self.last_name,
            "role": self.role.name if self.role else None,
            "status": self.status,
            "must_change_password": self.must_change_password,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class RefreshToken(db.Model):
    __tablename__ = "refresh_tokens"

    id = db.Column(db.Integer, primary_key=True)
    token_hash = db.Column(db.String(255), unique=True, nullable=False)
    user_id = db.Column(db.String(10), db.ForeignKey("users.user_id"), nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    revoked = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=utcnow)

    user = db.relationship("User", back_populates="refresh_tokens")

    __table_args__ = (db.Index("idx_refresh_token_hash", "token_hash"),)

    def is_valid(self):
        return not self.revoked and self.expires_at > utcnow()
