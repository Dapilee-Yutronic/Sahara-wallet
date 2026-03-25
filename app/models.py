from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from .database import Base


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    full_name = Column(String, nullable=False)
    hashed_password = Column(String, nullable=False)
    is_admin = Column(Boolean, default=False)
    kyc_status = Column(String, default="pending")
    created_at = Column(DateTime, default=datetime.utcnow)
    referral_code = Column(String(32), unique=True, index=True, nullable=True)
    referred_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    wallets = relationship("Wallet", back_populates="user")


class Wallet(Base):
    __tablename__ = "wallets"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    currency = Column(String, nullable=False)
    balance = Column(Float, default=0.0)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="wallets")


class LedgerEntry(Base):
    __tablename__ = "ledger_entries"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    entry_type = Column(String, nullable=False)
    currency = Column(String, nullable=False)
    amount = Column(Float, nullable=False)
    reference = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class FXQuote(Base):
    __tablename__ = "fx_quotes"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    from_currency = Column(String, nullable=False)
    to_currency = Column(String, nullable=False)
    source_amount = Column(Float, nullable=False)
    rate = Column(Float, nullable=False)
    fee = Column(Float, nullable=False)
    target_amount = Column(Float, nullable=False)
    status = Column(String, default="active")
    created_at = Column(DateTime, default=datetime.utcnow)


class Withdrawal(Base):
    __tablename__ = "withdrawals"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    currency = Column(String, nullable=False)
    amount = Column(Float, nullable=False)
    destination_type = Column(String, nullable=False)  # momo or bank
    destination_account = Column(String, nullable=False)
    status = Column(String, default="pending")
    created_at = Column(DateTime, default=datetime.utcnow)


class KYCSubmission(Base):
    __tablename__ = "kyc_submissions"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    passport_number = Column(String, nullable=False)
    passport_country = Column(String, nullable=False)
    residence_country = Column(String, nullable=False)
    current_city = Column(String, nullable=False)
    passport_image_url = Column(String, nullable=False)
    face_verification_image_url = Column(String, nullable=False, default="")
    status = Column(String, default="submitted")  # submitted, approved, rejected
    reviewer_notes = Column(String, default="")
    created_at = Column(DateTime, default=datetime.utcnow)
    reviewed_at = Column(DateTime, nullable=True)


class UserMedia(Base):
    __tablename__ = "user_media"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    media_type = Column(String, nullable=False)  # profile_photo, passport, face_verification
    file_url = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class LoginEvent(Base):
    __tablename__ = "login_events"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class UserSecurity(Base):
    __tablename__ = "user_security"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, unique=True, index=True)
    two_factor_enabled = Column(Boolean, default=False)
    two_factor_code_hash = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)


class PaymentMethod(Base):
    __tablename__ = "payment_methods"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    method_type = Column(String, nullable=False)  # card, bank
    provider = Column(String, nullable=False)  # visa, mastercard, bank_name
    last4 = Column(String, nullable=False)
    holder_name = Column(String, nullable=False)
    status = Column(String, default="active")
    created_at = Column(DateTime, default=datetime.utcnow)


class UserPublicProfile(Base):
    __tablename__ = "user_public_profiles"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, unique=True, index=True)
    public_id = Column(String, nullable=False, unique=True, index=True)
    alias = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)


class ReferralProgress(Base):
    """Demo referral: referee must reach qualifying USD inflow before referrer gets bonus."""

    __tablename__ = "referral_progress"
    id = Column(Integer, primary_key=True, index=True)
    referee_user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False, index=True)
    referrer_user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    qualifying_volume_usd = Column(Float, default=0.0)
    bonus_paid_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class AppSettings(Base):
    """Singleton row (id=1): FX payout/mid rates, FX fee %, PayPal demo commission %."""


    __tablename__ = "app_settings"
    id = Column(Integer, primary_key=True)
    payout_usd_ghs = Column(Float, nullable=False, default=14.25)
    payout_usd_ngn = Column(Float, nullable=False, default=1550.0)
    mid_market_usd_ghs = Column(Float, nullable=False, default=14.40)
    mid_market_usd_ngn = Column(Float, nullable=False, default=1580.0)
    fx_fee_percent = Column(Float, nullable=False, default=0.015)
    paypal_commission_percent = Column(Float, nullable=False, default=0.05)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class DemoPaypalInvoice(Base):
    __tablename__ = "demo_paypal_invoices"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    invoice_code = Column(String, unique=True, index=True, nullable=False)
    client_name = Column(String, nullable=False)
    client_email = Column(String, nullable=False)
    amount_usd = Column(Float, nullable=False)
    note = Column(String, default="")
    status = Column(String, default="pending")  # pending, paid, cancelled
    paid_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
