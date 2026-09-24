from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

BigIntPK = BigInteger().with_variant(Integer, "sqlite")


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    tg_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    username: Mapped[str | None] = mapped_column(Text)
    first_name: Mapped[str | None] = mapped_column(Text)
    lang: Mapped[str] = mapped_column(Text, nullable=False, default="en", server_default="en")
    balance_usd: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=Decimal("0"), server_default="0"
    )
    banned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    bot_blocked: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    accepted_terms: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    ref_source: Mapped[str | None] = mapped_column(Text)
    tz: Mapped[str] = mapped_column(Text, nullable=False, default="UTC", server_default="UTC")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    servers: Mapped[list[Server]] = relationship(back_populates="user")
    orders: Mapped[list[Order]] = relationship(back_populates="user")


class Admin(Base):
    __tablename__ = "admins"

    tg_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    added_by: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (CheckConstraint("role in ('owner','operator','support')", name="admins_role_chk"),)


class Server(Base):
    __tablename__ = "servers"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    partner_id: Mapped[str | None] = mapped_column(Text, unique=True)
    partner_name: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    location: Mapped[str] = mapped_column(Text, nullable=False)
    os_label: Mapped[str | None] = mapped_column(Text)
    plan_label: Mapped[str | None] = mapped_column(Text)
    login: Mapped[str | None] = mapped_column(Text)
    ip: Mapped[str | None] = mapped_column(Text)  # store as text for sqlite/tests; PG uses inet via migration
    cpu: Mapped[int | None] = mapped_column(Integer)
    ram_mb: Mapped[int | None] = mapped_column(Integer)
    disk_gb: Mapped[int | None] = mapped_column(Integer)
    partner_state: Mapped[str | None] = mapped_column(Text)
    rent_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    auto_renew: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    renew_days: Mapped[int] = mapped_column(Integer, nullable=False, default=30, server_default="30")
    cancelled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    frozen: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    missing: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    reminded_3d_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reminded_1d_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_expired_notice_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reinstall_pending: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    ready_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    partner_auto_renew_off: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    renew_prices: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship(back_populates="servers")

    __table_args__ = (
        CheckConstraint("renew_days in (2,7,30,90,180,365)", name="servers_renew_days_chk"),
        Index("ix_servers_user_id", "user_id"),
    )


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    server_id: Mapped[int | None] = mapped_column(ForeignKey("servers.id"))
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, server_default=text("'{}'"))
    location: Mapped[str | None] = mapped_column(Text)
    plan_id: Mapped[str | None] = mapped_column(Text)
    os_id: Mapped[str | None] = mapped_column(Text)
    months: Mapped[int | None] = mapped_column(Integer)
    renew_days: Mapped[int | None] = mapped_column(Integer)
    partner_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    markup: Mapped[Decimal | None] = mapped_column(Numeric(6, 3))
    fee: Mapped[Decimal | None] = mapped_column(Numeric(6, 3))
    price_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    charged_by_partner_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    payment_mode: Mapped[str] = mapped_column(Text, nullable=False, default="balance", server_default="balance")
    admin_id: Mapped[int | None] = mapped_column(BigInteger)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship(back_populates="orders")

    __table_args__ = (
        CheckConstraint(
            "kind in ('purchase','renew','reinstall','admin_purchase')",
            name="orders_kind_chk",
        ),
        CheckConstraint(
            "status in ('draft','queued','provisioning','waiting_partner_funds','active',"
            "'needs_review','failed','refunded','cancelled')",
            name="orders_status_chk",
        ),
        CheckConstraint(
            "payment_mode in ('balance','promo','admin')",
            name="orders_payment_mode_chk",
        ),
        Index("ix_orders_user_created", "user_id", "created_at"),
    )


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    class_: Mapped[str] = mapped_column("class", Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, server_default=text("'{}'"))
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending", server_default="pending")
    run_after: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=6, server_default="6")
    idem_key: Mapped[str | None] = mapped_column(Text)
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"))
    server_id: Mapped[int | None] = mapped_column(ForeignKey("servers.id"))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("class in ('purchase','manage','renew','retry')", name="jobs_class_chk"),
        CheckConstraint(
            "status in ('pending','running','done','failed','cancelled')",
            name="jobs_status_chk",
        ),
        Index("ix_jobs_status_run", "status", "run_after", "class"),
    )


class PartnerCall(Base):
    __tablename__ = "partner_calls"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    method: Mapped[str] = mapped_column(Text, nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(Text)
    request_id: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    job_id: Mapped[int | None] = mapped_column(BigInteger)
    job_class: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (Index("ix_partner_calls_method_at", "method", "at"),)


class LedgerEntry(Base):
    __tablename__ = "ledger_entries"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    amount_usd: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    ref_type: Mapped[str | None] = mapped_column(Text)
    ref_id: Mapped[int | None] = mapped_column(BigInteger)
    reason: Mapped[str | None] = mapped_column(Text)
    admin_id: Mapped[int | None] = mapped_column(BigInteger)
    uniq_key: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "kind in ('topup','purchase','renewal','refund','promo','adjust')",
            name="ledger_kind_chk",
        ),
        Index("ix_ledger_user_created", "user_id", "created_at"),
    )


class Invoice(Base):
    __tablename__ = "invoices"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    gateway: Mapped[str] = mapped_column(Text, nullable=False)
    gateway_invoice_id: Mapped[str | None] = mapped_column(Text, unique=True)
    amount_usd: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    credited_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    asset: Mapped[str | None] = mapped_column(Text)
    network: Mapped[str | None] = mapped_column(Text)
    pay_amount: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    address: Mapped[str | None] = mapped_column(Text)
    pay_url: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    txid: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "status in ('pending','paid','expired','partial','failed')",
            name="invoices_status_chk",
        ),
        Index("ix_invoices_user_created", "user_id", "created_at"),
        Index("ix_invoices_status_expires", "status", "expires_at"),
    )


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    key: Mapped[str] = mapped_column(Text, nullable=False)
    ref: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (UniqueConstraint("user_id", "key", "ref", name="uq_notifications_dedupe"),)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    actor_kind: Mapped[str] = mapped_column(Text, nullable=False)
    actor_id: Mapped[int | None] = mapped_column(BigInteger)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    subject_type: Mapped[str | None] = mapped_column(Text)
    subject_id: Mapped[int | None] = mapped_column(BigInteger)
    before: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    request_id: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint("actor_kind in ('user','admin','system')", name="audit_actor_chk"),
        Index("ix_audit_subject", "subject_type", "subject_id", "at"),
    )


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[Any] = mapped_column(JSON, nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_by: Mapped[int | None] = mapped_column(BigInteger)


class PromoCode(Base):
    __tablename__ = "promo_codes"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    amount_usd: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    max_uses: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    used_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PromoRedemption(Base):
    __tablename__ = "promo_redemptions"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    promo_id: Mapped[int] = mapped_column(ForeignKey("promo_codes.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (UniqueConstraint("user_id", "promo_id", name="uq_promo_user"),)
