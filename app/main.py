from datetime import datetime
import os
from uuid import uuid4

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.cors import CORSMiddleware
from sqlalchemy import func, inspect, text
from sqlalchemy.orm import Session

from .auth import create_access_token, get_current_user, hash_password, require_admin, verify_password
from .database import Base, engine, get_db
from .models import (
    DemoPaypalInvoice,
    FXQuote,
    KYCSubmission,
    LedgerEntry,
    LoginEvent,
    PaymentMethod,
    ReferralProgress,
    User,
    UserMedia,
    UserPublicProfile,
    UserSecurity,
    Wallet,
    Withdrawal,
)
from .schemas import (
    AddFundsRequest,
    AddPaymentMethodRequest,
    ConvertRequest,
    FXQuoteRequest,
    FXQuoteResponse,
    KYCSubmissionRequest,
    LoginRequest,
    ProfileResponse,
    RegisterRequest,
    TokenResponse,
    WithdrawRequest,
    WithdrawalResponse,
)

app = FastAPI(title="Global Wallet MVP", version="0.1.0")

# Lets browsers call the API from another origin (demo tunnels, mobile, etc.)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

Base.metadata.create_all(bind=engine)
os.makedirs("static/uploads", exist_ok=True)


def _sqlite_add_kyc_face_column() -> None:
    """Add face_verification_image_url to existing SQLite DBs (no-op if already present)."""
    try:
        insp = inspect(engine)
        if not insp.has_table("kyc_submissions"):
            return
        cols = [c["name"] for c in insp.get_columns("kyc_submissions")]
        if "face_verification_image_url" in cols:
            return
        with engine.begin() as conn:
            conn.execute(
                text(
                    "ALTER TABLE kyc_submissions ADD COLUMN face_verification_image_url VARCHAR(512) DEFAULT ''"
                )
            )
    except Exception:
        pass


_sqlite_add_kyc_face_column()

app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/uploads", StaticFiles(directory="static/uploads"), name="uploads")

# Simple pricing constants for MVP.
MID_MARKET_USD_GHS = 14.40
PAYOUT_USD_GHS = 14.25
FX_FEE_PERCENT = 0.015
PAYPAL_DEMO_COMMISSION_PERCENT = 0.05
PUBLIC_ID_PREFIX = "SAHARA"
DEFAULT_ADMIN_EMAIL = "admin@globalwallet.app"
DEFAULT_ADMIN_PASSWORD = "Admin123!"
UPLOAD_DIR = "static/uploads"
ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


def normalize_email(email: str) -> str:
    """Lowercase + strip so login matches registration regardless of client casing."""
    return (email or "").strip().lower()


def normalize_stored_user_emails(db: Session) -> None:
    """One-time fix per startup: legacy rows may have mixed-case emails; login uses lowercase."""
    for u in db.query(User).all():
        if not u.email:
            continue
        ne = normalize_email(u.email)
        if u.email == ne:
            continue
        conflict = (
            db.query(User)
            .filter(User.id != u.id, func.lower(User.email) == ne)
            .first()
        )
        if conflict:
            continue
        u.email = ne
    db.commit()


# Demo referral program (testers can verify end-to-end).
REFERRAL_QUALIFY_USD = 100.0
REFERRAL_BONUS_USD = 20.0


def _sqlite_migrate_referrals() -> None:
    try:
        insp = inspect(engine)
        if not insp.has_table("users"):
            return
        cols = [c["name"] for c in insp.get_columns("users")]
        with engine.begin() as conn:
            if "referral_code" not in cols:
                conn.execute(text("ALTER TABLE users ADD COLUMN referral_code VARCHAR(32)"))
            if "referred_by_user_id" not in cols:
                conn.execute(text("ALTER TABLE users ADD COLUMN referred_by_user_id INTEGER"))
    except Exception:
        pass


_sqlite_migrate_referrals()


def ensure_user_referral_code(db: Session, user: User) -> str:
    if user.referral_code:
        return user.referral_code
    for _ in range(48):
        code = uuid4().hex[:8].upper()
        clash = db.query(User).filter(User.referral_code == code).first()
        if not clash:
            user.referral_code = code
            db.commit()
            db.refresh(user)
            return code
    raise HTTPException(status_code=500, detail="Could not assign referral code")


def record_referral_qualifying_usd(db: Session, referee_user_id: int, usd_amount: float) -> None:
    """Count inbound USD activity for referred users; credit referrer when threshold is met (demo)."""
    if usd_amount <= 0:
        return
    progress = (
        db.query(ReferralProgress)
        .filter(ReferralProgress.referee_user_id == referee_user_id, ReferralProgress.bonus_paid_at.is_(None))
        .first()
    )
    if not progress:
        return
    progress.qualifying_volume_usd = round(progress.qualifying_volume_usd + usd_amount, 2)
    if progress.qualifying_volume_usd >= REFERRAL_QUALIFY_USD:
        referrer = db.query(User).filter(User.id == progress.referrer_user_id).first()
        if referrer:
            rw = get_or_create_wallet(db, referrer.id, "USD")
            rw.balance = round(rw.balance + REFERRAL_BONUS_USD, 2)
            post_ledger(
                db,
                referrer.id,
                "REFERRAL_BONUS",
                "USD",
                REFERRAL_BONUS_USD,
                f"REFBONUS:referee_user_id={referee_user_id}",
            )
        progress.bonus_paid_at = datetime.utcnow()
    db.flush()


@app.get("/health")
def health():
    """Public health check — use this to verify the server is reachable when sharing a link."""
    return {"status": "ok", "service": "global-wallet-mvp"}


@app.get("/")
def home():
    return FileResponse("static/index.html")


@app.get("/admin-panel")
def admin_panel():
    return FileResponse("static/admin.html")


def save_upload(file: UploadFile) -> str:
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in ALLOWED_IMAGE_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Only jpg, jpeg, png, webp allowed")
    file_name = f"{uuid4().hex}{ext}"
    file_path = os.path.join(UPLOAD_DIR, file_name)
    with open(file_path, "wb") as out:
        out.write(file.file.read())
    return f"/uploads/{file_name}"


def get_latest_profile_photo(db: Session, user_id: int) -> str | None:
    row = (
        db.query(UserMedia)
        .filter(UserMedia.user_id == user_id, UserMedia.media_type == "profile_photo")
        .order_by(UserMedia.created_at.desc())
        .first()
    )
    return row.file_url if row else None


def get_or_create_wallet(db: Session, user_id: int, currency: str) -> Wallet:
    wallet = db.query(Wallet).filter(Wallet.user_id == user_id, Wallet.currency == currency).first()
    if wallet:
        return wallet
    wallet = Wallet(user_id=user_id, currency=currency, balance=0.0)
    db.add(wallet)
    db.commit()
    db.refresh(wallet)
    return wallet


def post_ledger(db: Session, user_id: int, entry_type: str, currency: str, amount: float, reference: str):
    entry = LedgerEntry(
        user_id=user_id,
        entry_type=entry_type,
        currency=currency,
        amount=amount,
        reference=reference,
    )
    db.add(entry)


def generate_demo_invoice_code() -> str:
    return f"SHR-{uuid4().hex[:10].upper()}"


def base_public_id_for_user(user_id: int) -> str:
    return f"{PUBLIC_ID_PREFIX}-{user_id:06d}"


def pay_handle_from_profile(row: UserPublicProfile) -> str:
    # Keep @sahara.com permanent; alias is optional human-friendly prefix.
    if row.alias:
        return f"{row.alias}@sahara.com"
    return f"user{row.user_id:06d}@sahara.com"


def ensure_user_public_profile(db: Session, user: User) -> UserPublicProfile:
    row = db.query(UserPublicProfile).filter(UserPublicProfile.user_id == user.id).first()
    if row:
        base_id = base_public_id_for_user(user.id)
        # Keep brand prefix fixed for legacy/demo rows too.
        if not (row.public_id or "").startswith(f"{PUBLIC_ID_PREFIX}-"):
            row.public_id = base_id if not row.alias else f"{base_id}-{row.alias}"
            row.updated_at = datetime.utcnow()
            db.commit()
            db.refresh(row)
        return row
    row = UserPublicProfile(
        user_id=user.id,
        public_id=base_public_id_for_user(user.id),
        alias=None,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def ensure_user_security(db: Session, user: User) -> UserSecurity:
    row = db.query(UserSecurity).filter(UserSecurity.user_id == user.id).first()
    if row:
        return row
    row = UserSecurity(user_id=user.id, two_factor_enabled=False, two_factor_code_hash=None)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def wallet_summary(db: Session, user_id: int) -> dict:
    usd_wallet = get_or_create_wallet(db, user_id, "USD")
    ghs_wallet = get_or_create_wallet(db, user_id, "GHS")
    ghs_as_usd = ghs_wallet.balance / PAYOUT_USD_GHS if PAYOUT_USD_GHS else 0.0
    total_usd_equivalent = usd_wallet.balance + ghs_as_usd
    return {
        "usd_balance": round(usd_wallet.balance, 2),
        "ghs_balance": round(ghs_wallet.balance, 2),
        "ghs_usd_equivalent": round(ghs_as_usd, 2),
        "total_usd_equivalent": round(total_usd_equivalent, 2),
        "display_rate_usd_to_ghs": PAYOUT_USD_GHS,
    }


@app.on_event("startup")
def seed_admin():
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    db = next(get_db())
    try:
        admin = db.query(User).filter(func.lower(User.email) == normalize_email(DEFAULT_ADMIN_EMAIL)).first()
        if admin:
            if not admin.is_admin:
                admin.is_admin = True
            admin.hashed_password = hash_password(DEFAULT_ADMIN_PASSWORD)
            db.commit()
            get_or_create_wallet(db, admin.id, "USD")
            get_or_create_wallet(db, admin.id, "GHS")
        else:
            admin = User(
                email=DEFAULT_ADMIN_EMAIL,
                full_name="Admin",
                hashed_password=hash_password(DEFAULT_ADMIN_PASSWORD),
                is_admin=True,
                kyc_status="approved",
            )
            db.add(admin)
            db.commit()
            db.refresh(admin)
            get_or_create_wallet(db, admin.id, "USD")
            get_or_create_wallet(db, admin.id, "GHS")

        for u in db.query(User).all():
            if not u.referral_code:
                ensure_user_referral_code(db, u)
        normalize_stored_user_emails(db)
    finally:
        db.close()


@app.post("/auth/register", response_model=TokenResponse)
def register(payload: RegisterRequest, db: Session = Depends(get_db)):
    norm_email = normalize_email(str(payload.email))
    if not norm_email:
        raise HTTPException(status_code=400, detail="Invalid email")
    existing = db.query(User).filter(func.lower(User.email) == norm_email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")

    raw_ref = (payload.referral_code or "").strip().upper()
    referrer = None
    if raw_ref:
        referrer = db.query(User).filter(User.referral_code == raw_ref).first()
        if not referrer:
            raise HTTPException(status_code=400, detail="Invalid referral code")

    user = User(
        email=norm_email,
        full_name=payload.full_name,
        hashed_password=hash_password(payload.password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    ensure_user_referral_code(db, user)

    if referrer:
        fresh = db.query(User).filter(User.id == user.id).first()
        if not fresh or fresh.id == referrer.id:
            raise HTTPException(status_code=400, detail="Invalid referral code")
        fresh.referred_by_user_id = referrer.id
        db.add(
            ReferralProgress(
                referee_user_id=fresh.id,
                referrer_user_id=referrer.id,
                qualifying_volume_usd=0.0,
            )
        )
        db.commit()

    get_or_create_wallet(db, user.id, "USD")
    get_or_create_wallet(db, user.id, "GHS")

    token = create_access_token({"sub": str(user.id)})
    return TokenResponse(access_token=token)


@app.post("/auth/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    norm_email = normalize_email(payload.email)
    if not norm_email:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    user = db.query(User).filter(func.lower(User.email) == norm_email).first()
    if not user or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    security = ensure_user_security(db, user)
    if security.two_factor_enabled:
        otp = (payload.otp or "").strip()
        if not otp:
            raise HTTPException(status_code=401, detail="2FA code required")
        if not security.two_factor_code_hash or not verify_password(otp, security.two_factor_code_hash):
            raise HTTPException(status_code=401, detail="Invalid 2FA code")
    db.add(LoginEvent(user_id=user.id))
    db.commit()
    token = create_access_token({"sub": str(user.id)})
    return TokenResponse(access_token=token)


@app.get("/me", response_model=ProfileResponse)
def me(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    wallets = db.query(Wallet).filter(Wallet.user_id == current_user.id).all()
    public_profile = ensure_user_public_profile(db, current_user)
    return ProfileResponse(
        id=current_user.id,
        email=current_user.email,
        full_name=current_user.full_name,
        is_admin=current_user.is_admin,
        kyc_status=current_user.kyc_status,
        profile_photo_url=get_latest_profile_photo(db, current_user.id),
        public_id=public_profile.public_id,
        pay_handle=pay_handle_from_profile(public_profile),
        wallets=[{"currency": w.currency, "balance": round(w.balance, 2)} for w in wallets],
    )


@app.get("/me/referral")
def referral_status(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Demo referral: share code; referee activity unlocks $20 to referrer after $100 qualifying USD inflow."""
    ensure_user_referral_code(db, current_user)
    progress = (
        db.query(ReferralProgress).filter(ReferralProgress.referee_user_id == current_user.id).first()
    )
    as_referee = None
    if progress:
        as_referee = {
            "volume_usd": round(progress.qualifying_volume_usd, 2),
            "threshold_usd": REFERRAL_QUALIFY_USD,
            "bonus_usd": REFERRAL_BONUS_USD,
            "qualified": progress.qualifying_volume_usd >= REFERRAL_QUALIFY_USD,
            "bonus_paid_to_referrer": progress.bonus_paid_at is not None,
        }
    paid_rows = (
        db.query(ReferralProgress)
        .filter(ReferralProgress.referrer_user_id == current_user.id, ReferralProgress.bonus_paid_at.isnot(None))
        .all()
    )
    pending = (
        db.query(ReferralProgress)
        .filter(ReferralProgress.referrer_user_id == current_user.id, ReferralProgress.bonus_paid_at.is_(None))
        .all()
    )
    return {
        "my_referral_code": current_user.referral_code,
        "threshold_usd": REFERRAL_QUALIFY_USD,
        "bonus_usd": REFERRAL_BONUS_USD,
        "as_referee": as_referee,
        "as_referrer": {
            "completed_referrals": len(paid_rows),
            "total_bonus_usd": round(len(paid_rows) * REFERRAL_BONUS_USD, 2),
            "pending_referees": len(pending),
        },
    }


@app.get("/me/public-id")
def my_public_id(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = ensure_user_public_profile(db, current_user)
    return {
        "public_id": row.public_id,
        "pay_handle": pay_handle_from_profile(row),
        "alias": row.alias,
        "base_id": base_public_id_for_user(current_user.id),
    }


@app.get("/me/security")
def my_security(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = ensure_user_security(db, current_user)
    return {"two_factor_enabled": row.two_factor_enabled}


@app.post("/me/security/2fa/enable")
def enable_two_factor(
    otp_code: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    code = otp_code.strip()
    if len(code) != 6 or not code.isdigit():
        raise HTTPException(status_code=400, detail="2FA code must be a 6-digit number")
    row = ensure_user_security(db, current_user)
    row.two_factor_enabled = True
    row.two_factor_code_hash = hash_password(code)
    row.updated_at = datetime.utcnow()
    db.commit()
    return {"message": "2FA enabled for your account"}


@app.post("/me/security/2fa/disable")
def disable_two_factor(
    otp_code: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = ensure_user_security(db, current_user)
    if not row.two_factor_enabled:
        return {"message": "2FA already disabled"}
    code = otp_code.strip()
    if not row.two_factor_code_hash or not verify_password(code, row.two_factor_code_hash):
        raise HTTPException(status_code=400, detail="Invalid current 2FA code")
    row.two_factor_enabled = False
    row.two_factor_code_hash = None
    row.updated_at = datetime.utcnow()
    db.commit()
    return {"message": "2FA disabled"}


@app.patch("/me/public-id")
def update_my_public_id(
    alias: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    cleaned = alias.strip().lower()
    if len(cleaned) < 3 or len(cleaned) > 16:
        raise HTTPException(status_code=400, detail="Alias must be between 3 and 16 characters")
    allowed = set("abcdefghijklmnopqrstuvwxyz0123456789-_")
    if any(ch not in allowed for ch in cleaned):
        raise HTTPException(status_code=400, detail="Alias can contain only letters, numbers, dash, underscore")

    base_id = base_public_id_for_user(current_user.id)
    new_public_id = f"{base_id}-{cleaned}"
    existing = (
        db.query(UserPublicProfile)
        .filter(UserPublicProfile.public_id == new_public_id, UserPublicProfile.user_id != current_user.id)
        .first()
    )
    if existing:
        raise HTTPException(status_code=400, detail="This alias is already taken")

    row = ensure_user_public_profile(db, current_user)
    row.alias = cleaned
    row.public_id = new_public_id
    row.updated_at = datetime.utcnow()
    db.commit()
    return {
        "message": "Public ID updated",
        "public_id": row.public_id,
        "pay_handle": pay_handle_from_profile(row),
        "alias": row.alias,
        "base_id": base_id,
    }


@app.post("/files/upload")
def upload_file(file: UploadFile = File(...), current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    file_url = save_upload(file)
    db.add(UserMedia(user_id=current_user.id, media_type="generic", file_url=file_url))
    db.commit()
    return {"file_url": file_url}


@app.post("/me/photo")
def upload_profile_photo(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    file_url = save_upload(file)
    db.add(UserMedia(user_id=current_user.id, media_type="profile_photo", file_url=file_url))
    db.commit()
    return {"message": "Profile photo uploaded", "file_url": file_url}


@app.post("/me/payment-method")
def add_payment_method(
    payload: AddPaymentMethodRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    method_type = payload.method_type.strip().lower()
    if method_type not in ("card", "bank"):
        raise HTTPException(status_code=400, detail="method_type must be card or bank")
    digits = "".join(ch for ch in payload.account_number if ch.isdigit())
    if len(digits) < 4:
        raise HTTPException(status_code=400, detail="Invalid account/card number")

    row = PaymentMethod(
        user_id=current_user.id,
        method_type=method_type,
        provider=payload.provider.strip(),
        last4=digits[-4:],
        holder_name=payload.holder_name.strip(),
        status="active",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {
        "message": "Payment method added",
        "payment_method": {
            "id": row.id,
            "method_type": row.method_type,
            "provider": row.provider,
            "last4": row.last4,
            "holder_name": row.holder_name,
            "status": row.status,
            "created_at": row.created_at,
        },
    }


@app.get("/me/payment-methods")
def my_payment_methods(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = (
        db.query(PaymentMethod)
        .filter(PaymentMethod.user_id == current_user.id)
        .order_by(PaymentMethod.created_at.desc())
        .all()
    )
    return [
        {
            "id": r.id,
            "method_type": r.method_type,
            "provider": r.provider,
            "last4": r.last4,
            "holder_name": r.holder_name,
            "status": r.status,
            "created_at": r.created_at,
        }
        for r in rows
    ]


@app.post("/wallet/add-funds")
def add_funds(payload: AddFundsRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if payload.amount_usd <= 0:
        raise HTTPException(status_code=400, detail="Amount must be positive")
    usd_wallet = get_or_create_wallet(db, current_user.id, "USD")
    usd_wallet.balance += payload.amount_usd
    post_ledger(db, current_user.id, "CREDIT_INBOUND", "USD", payload.amount_usd, payload.reference)
    record_referral_qualifying_usd(db, current_user.id, payload.amount_usd)
    db.commit()
    return {"message": "Funds credited", "new_usd_balance": round(usd_wallet.balance, 2)}


@app.post("/demo/paypal/receive")
def demo_paypal_receive(
    amount_usd: float,
    payer_email: str = "",
    note: str = "",
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if amount_usd <= 0:
        raise HTTPException(status_code=400, detail="Amount must be positive")

    usd_wallet = get_or_create_wallet(db, current_user.id, "USD")
    commission_usd = round(amount_usd * PAYPAL_DEMO_COMMISSION_PERCENT, 2)
    net_amount_usd = round(max(amount_usd - commission_usd, 0), 2)

    source_label = payer_email.strip() or "paypal-client@demo.test"
    ref_note = note.strip() or "PayPal demo inbound payment"
    inbound_reference = f"PAYPAL_DEMO:{source_label}:{ref_note}"

    usd_wallet.balance += net_amount_usd
    post_ledger(db, current_user.id, "CREDIT_INBOUND", "USD", net_amount_usd, inbound_reference)
    post_ledger(db, current_user.id, "PAYPAL_COMMISSION", "USD", -commission_usd, inbound_reference)
    record_referral_qualifying_usd(db, current_user.id, net_amount_usd)
    db.commit()

    return {
        "message": "Demo PayPal payment received and credited",
        "gross_amount_usd": round(amount_usd, 2),
        "paypal_commission_usd": commission_usd,
        "net_credited_usd": net_amount_usd,
        "payer_email": source_label,
        "reference": inbound_reference,
        "wallet_summary": wallet_summary(db, current_user.id),
    }


@app.post("/demo/paypal/invoices")
def create_demo_paypal_invoice(
    amount_usd: float,
    client_name: str,
    client_email: str,
    note: str = "",
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if amount_usd <= 0:
        raise HTTPException(status_code=400, detail="Amount must be positive")
    if not client_name.strip():
        raise HTTPException(status_code=400, detail="client_name is required")
    if not client_email.strip():
        raise HTTPException(status_code=400, detail="client_email is required")

    invoice = DemoPaypalInvoice(
        user_id=current_user.id,
        invoice_code=generate_demo_invoice_code(),
        client_name=client_name.strip(),
        client_email=client_email.strip().lower(),
        amount_usd=round(amount_usd, 2),
        note=note.strip(),
        status="pending",
    )
    db.add(invoice)
    db.commit()
    db.refresh(invoice)
    return {
        "message": "Demo PayPal invoice created",
        "invoice": {
            "invoice_code": invoice.invoice_code,
            "amount_usd": invoice.amount_usd,
            "client_name": invoice.client_name,
            "client_email": invoice.client_email,
            "note": invoice.note,
            "status": invoice.status,
            "created_at": invoice.created_at,
            "pay_link_demo": f"/demo/paypal/invoices/{invoice.invoice_code}/pay",
        },
    }


@app.get("/demo/paypal/invoices")
def list_demo_paypal_invoices(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = (
        db.query(DemoPaypalInvoice)
        .filter(DemoPaypalInvoice.user_id == current_user.id)
        .order_by(DemoPaypalInvoice.created_at.desc())
        .all()
    )
    return [
        {
            "invoice_code": r.invoice_code,
            "amount_usd": round(r.amount_usd, 2),
            "client_name": r.client_name,
            "client_email": r.client_email,
            "note": r.note,
            "status": r.status,
            "paid_at": r.paid_at,
            "created_at": r.created_at,
        }
        for r in rows
    ]


@app.post("/demo/paypal/invoices/{invoice_code}/pay")
def pay_demo_paypal_invoice(
    invoice_code: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    invoice = (
        db.query(DemoPaypalInvoice)
        .filter(
            DemoPaypalInvoice.user_id == current_user.id,
            DemoPaypalInvoice.invoice_code == invoice_code.strip(),
        )
        .first()
    )
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if invoice.status == "paid":
        raise HTTPException(status_code=400, detail="Invoice already paid")

    gross_usd = round(float(invoice.amount_usd), 2)
    commission_usd = round(gross_usd * PAYPAL_DEMO_COMMISSION_PERCENT, 2)
    net_amount_usd = round(max(gross_usd - commission_usd, 0), 2)

    usd_wallet = get_or_create_wallet(db, current_user.id, "USD")
    usd_wallet.balance += net_amount_usd

    ref = f"PAYPAL_INVOICE:{invoice.invoice_code}:{invoice.client_email}"
    post_ledger(db, current_user.id, "CREDIT_INBOUND", "USD", net_amount_usd, ref)
    post_ledger(db, current_user.id, "PAYPAL_COMMISSION", "USD", -commission_usd, ref)
    post_ledger(db, current_user.id, "PAYPAL_INVOICE_PAID", "USD", gross_usd, ref)

    invoice.status = "paid"
    invoice.paid_at = datetime.utcnow()
    record_referral_qualifying_usd(db, current_user.id, net_amount_usd)
    db.commit()

    return {
        "message": "Demo PayPal invoice paid and wallet credited",
        "invoice_code": invoice.invoice_code,
        "gross_amount_usd": gross_usd,
        "paypal_commission_usd": commission_usd,
        "net_credited_usd": net_amount_usd,
        "wallet_summary": wallet_summary(db, current_user.id),
    }


@app.post("/fx/quote", response_model=FXQuoteResponse)
def create_quote(payload: FXQuoteRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if payload.amount_usd <= 0:
        raise HTTPException(status_code=400, detail="Amount must be positive")
    rate = PAYOUT_USD_GHS
    fee = round(payload.amount_usd * FX_FEE_PERCENT, 2)
    net = max(payload.amount_usd - fee, 0)
    target = round(net * rate, 2)
    gross_target_mid_market = round(payload.amount_usd * MID_MARKET_USD_GHS, 2)
    target_at_payout_rate = round(payload.amount_usd * PAYOUT_USD_GHS, 2)
    spread_gain_ghs = round(target_at_payout_rate - target, 2)
    operator_gross_revenue_ghs = round(gross_target_mid_market - target, 2)
    quote = FXQuote(
        user_id=current_user.id,
        from_currency="USD",
        to_currency="GHS",
        source_amount=payload.amount_usd,
        rate=rate,
        fee=fee,
        target_amount=target,
    )
    db.add(quote)
    db.commit()
    db.refresh(quote)
    response = FXQuoteResponse(quote_id=quote.id, rate=rate, fee=fee, target_amount_ghs=target).model_dump()
    response.update(
        {
            "mid_market_rate": MID_MARKET_USD_GHS,
            "spread_gain_ghs": spread_gain_ghs,
            "operator_gross_revenue_ghs": operator_gross_revenue_ghs,
        }
    )
    return response


@app.post("/fx/convert")
def convert_quote(payload: ConvertRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    quote = db.query(FXQuote).filter(FXQuote.id == payload.quote_id, FXQuote.user_id == current_user.id).first()
    if not quote or quote.status != "active":
        raise HTTPException(status_code=404, detail="Quote not found or inactive")

    usd_wallet = get_or_create_wallet(db, current_user.id, "USD")
    ghs_wallet = get_or_create_wallet(db, current_user.id, "GHS")

    if usd_wallet.balance < quote.source_amount:
        raise HTTPException(status_code=400, detail="Insufficient USD balance")

    usd_wallet.balance -= quote.source_amount
    ghs_wallet.balance += quote.target_amount
    quote.status = "executed"

    post_ledger(db, current_user.id, "FX_DEBIT", "USD", -quote.source_amount, f"FX#{quote.id}")
    post_ledger(db, current_user.id, "FX_CREDIT", "GHS", quote.target_amount, f"FX#{quote.id}")
    post_ledger(db, current_user.id, "FX_FEE", "USD", -quote.fee, f"FX#{quote.id}")
    db.commit()

    source_mid_market_ghs = round(quote.source_amount * MID_MARKET_USD_GHS, 2)
    operator_revenue_ghs = round(source_mid_market_ghs - quote.target_amount, 2)
    return {
        "message": "Conversion successful",
        "operator_revenue_ghs": operator_revenue_ghs,
        "operator_revenue_usd_equivalent": round(operator_revenue_ghs / PAYOUT_USD_GHS, 2),
        "wallet_summary": wallet_summary(db, current_user.id),
    }


@app.post("/withdrawals", response_model=WithdrawalResponse)
def request_withdrawal(
    payload: WithdrawRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if payload.amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be positive")
    if payload.currency not in ("USD", "GHS"):
        raise HTTPException(status_code=400, detail="Unsupported currency")
    if payload.destination_type not in ("momo", "bank"):
        raise HTTPException(status_code=400, detail="destination_type must be momo or bank")

    wallet = get_or_create_wallet(db, current_user.id, payload.currency)
    if wallet.balance < payload.amount:
        raise HTTPException(status_code=400, detail="Insufficient balance")

    wallet.balance -= payload.amount
    w = Withdrawal(
        user_id=current_user.id,
        currency=payload.currency,
        amount=payload.amount,
        destination_type=payload.destination_type,
        destination_account=payload.destination_account,
        status="processing",
    )
    db.add(w)
    db.flush()
    post_ledger(db, current_user.id, "WITHDRAWAL_DEBIT", payload.currency, -payload.amount, f"WD#{w.id}")
    db.commit()
    db.refresh(w)
    response = WithdrawalResponse(
        withdrawal_id=w.id,
        status=w.status,
        currency=w.currency,
        amount=w.amount,
        destination_type=w.destination_type,
        created_at=w.created_at,
    ).model_dump()
    response.update({"wallet_summary": wallet_summary(db, current_user.id)})
    return response


@app.post("/transfers/sahara")
def transfer_to_sahara_user(
    to_pay_handle: str,
    amount_usd: float,
    note: str = "",
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if amount_usd <= 0:
        raise HTTPException(status_code=400, detail="Amount must be positive")

    target_handle = to_pay_handle.strip().lower()
    if not target_handle.endswith("@sahara.com"):
        raise HTTPException(status_code=400, detail="Destination must be a valid @sahara.com pay address")

    sender_profile = ensure_user_public_profile(db, current_user)
    if pay_handle_from_profile(sender_profile).lower() == target_handle:
        raise HTTPException(status_code=400, detail="You cannot transfer to your own Sahara address")

    recipient_profile = db.query(UserPublicProfile).all()
    recipient_profile = next((p for p in recipient_profile if pay_handle_from_profile(p).lower() == target_handle), None)
    if not recipient_profile:
        raise HTTPException(status_code=404, detail="Recipient Sahara address not found")

    recipient_user = db.query(User).filter(User.id == recipient_profile.user_id).first()
    if not recipient_user:
        raise HTTPException(status_code=404, detail="Recipient user not found")

    sender_usd_wallet = get_or_create_wallet(db, current_user.id, "USD")
    if sender_usd_wallet.balance < amount_usd:
        raise HTTPException(status_code=400, detail="Insufficient USD balance")

    recipient_usd_wallet = get_or_create_wallet(db, recipient_user.id, "USD")
    sender_usd_wallet.balance -= amount_usd
    recipient_usd_wallet.balance += amount_usd

    clean_note = note.strip() or "Sahara wallet transfer"
    sender_name = (current_user.full_name or "").strip() or pay_handle_from_profile(sender_profile)
    ref = f"S2S:{sender_name}|{pay_handle_from_profile(sender_profile)}->{target_handle}:{clean_note}"
    post_ledger(db, current_user.id, "TRANSFER_OUT", "USD", -amount_usd, ref)
    post_ledger(db, recipient_user.id, "TRANSFER_IN", "USD", amount_usd, ref)
    record_referral_qualifying_usd(db, recipient_user.id, amount_usd)
    db.commit()

    return {
        "message": "Transfer completed",
        "to_pay_handle": target_handle,
        "amount_usd": round(amount_usd, 2),
        "note": clean_note,
        "wallet_summary": wallet_summary(db, current_user.id),
    }


@app.get("/transfers/sahara/verify")
def verify_sahara_recipient(
    to_pay_handle: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    target_handle = to_pay_handle.strip().lower()
    if not target_handle.endswith("@sahara.com"):
        raise HTTPException(status_code=400, detail="Destination must be a valid @sahara.com pay address")

    sender_profile = ensure_user_public_profile(db, current_user)
    sender_handle = pay_handle_from_profile(sender_profile).lower()
    if sender_handle == target_handle:
        raise HTTPException(status_code=400, detail="You cannot transfer to your own Sahara address")

    profiles = db.query(UserPublicProfile).all()
    recipient_profile = next((p for p in profiles if pay_handle_from_profile(p).lower() == target_handle), None)
    if not recipient_profile:
        raise HTTPException(status_code=404, detail="Recipient Sahara address not found")
    recipient_user = db.query(User).filter(User.id == recipient_profile.user_id).first()
    if not recipient_user:
        raise HTTPException(status_code=404, detail="Recipient user not found")

    names = (recipient_user.full_name or "").strip().split()
    first_name = names[0] if names else recipient_user.full_name
    last_name = " ".join(names[1:]) if len(names) > 1 else ""
    return {
        "pay_handle": target_handle,
        "recipient_first_name": first_name,
        "recipient_last_name": last_name,
        "recipient_full_name": recipient_user.full_name,
        "verified": True,
    }


@app.get("/wallet/summary")
def get_wallet_summary(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return wallet_summary(db, current_user.id)


@app.post("/kyc/submit")
def submit_kyc(payload: KYCSubmissionRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not payload.passport_number.strip():
        raise HTTPException(status_code=400, detail="passport_number is required")
    if not payload.passport_image_url.strip():
        raise HTTPException(status_code=400, detail="passport_image_url is required")
    if not payload.face_verification_image_url.strip():
        raise HTTPException(
            status_code=400,
            detail="face_verification_image_url is required — upload a clear selfie for identity verification",
        )

    row = KYCSubmission(
        user_id=current_user.id,
        passport_number=payload.passport_number.strip(),
        passport_country=payload.passport_country.strip(),
        residence_country=payload.residence_country.strip(),
        current_city=payload.current_city.strip(),
        passport_image_url=payload.passport_image_url.strip(),
        face_verification_image_url=payload.face_verification_image_url.strip(),
        status="submitted",
    )
    current_user.kyc_status = "pending_review"
    db.add(row)
    db.commit()
    db.refresh(row)
    return {
        "message": "KYC submitted",
        "submission_id": row.id,
        "status": row.status,
        "kyc_status": current_user.kyc_status,
    }


@app.get("/kyc/me")
def my_kyc(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = (
        db.query(KYCSubmission)
        .filter(KYCSubmission.user_id == current_user.id)
        .order_by(KYCSubmission.created_at.desc())
        .all()
    )
    return [
        {
            "id": r.id,
            "passport_country": r.passport_country,
            "residence_country": r.residence_country,
            "current_city": r.current_city,
            "passport_image_url": r.passport_image_url,
            "face_verification_image_url": r.face_verification_image_url or "",
            "status": r.status,
            "reviewer_notes": r.reviewer_notes,
            "created_at": r.created_at,
            "reviewed_at": r.reviewed_at,
        }
        for r in rows
    ]


@app.get("/transactions")
def transactions(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    entries = (
        db.query(LedgerEntry)
        .filter(LedgerEntry.user_id == current_user.id)
        .order_by(LedgerEntry.created_at.desc())
        .limit(100)
        .all()
    )
    return [
        {
            "id": e.id,
            "type": e.entry_type,
            "currency": e.currency,
            "amount": round(e.amount, 2),
            "reference": e.reference,
            "created_at": e.created_at,
        }
        for e in entries
    ]


@app.get("/me/activity")
def my_activity(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    ledger = (
        db.query(LedgerEntry)
        .filter(LedgerEntry.user_id == current_user.id)
        .order_by(LedgerEntry.created_at.desc())
        .limit(120)
        .all()
    )
    withdrawals = (
        db.query(Withdrawal)
        .filter(Withdrawal.user_id == current_user.id)
        .order_by(Withdrawal.created_at.desc())
        .limit(60)
        .all()
    )

    # Build a handle -> full_name map once for richer transfer messages.
    handle_to_name = {}
    for p in db.query(UserPublicProfile).all():
        try:
            handle = pay_handle_from_profile(p).lower()
            u = db.query(User).filter(User.id == p.user_id).first()
            if handle and u and u.full_name:
                handle_to_name[handle] = u.full_name
        except Exception:
            continue

    events = []
    for e in ledger:
        summary = f"Activity on your wallet ({e.entry_type})."
        if e.entry_type == "CREDIT_INBOUND":
            summary = f"You received {round(e.amount, 2)} {e.currency} from {e.reference}."
        elif e.entry_type == "FX_CREDIT":
            summary = f"You converted funds and got credited {round(e.amount, 2)} {e.currency}."
        elif e.entry_type == "FX_DEBIT":
            summary = f"You converted {abs(round(e.amount, 2))} {e.currency} from your wallet."
        elif e.entry_type == "WITHDRAWAL_DEBIT":
            summary = f"You initiated a withdrawal of {abs(round(e.amount, 2))} {e.currency}."
        elif e.entry_type == "FX_FEE":
            summary = f"A conversion fee of {abs(round(e.amount, 2))} {e.currency} was charged."
        elif e.entry_type == "PAYPAL_COMMISSION":
            summary = f"Sahara charged a PayPal processing commission of {abs(round(e.amount, 2))} {e.currency}."
        elif e.entry_type == "PAYPAL_INVOICE_PAID":
            summary = f"Your PayPal invoice was marked paid for {round(e.amount, 2)} {e.currency} (gross)."
        elif e.entry_type == "TRANSFER_OUT":
            recipient_name = "another Sahara user"
            if e.reference and e.reference.startswith("S2S:"):
                try:
                    header = e.reference.split(":", 2)[1]
                    recipient_handle = header.split("->", 1)[1].strip().lower()
                    recipient_name = handle_to_name.get(recipient_handle, recipient_name)
                except Exception:
                    recipient_name = "another Sahara user"
            summary = f"You sent {abs(round(e.amount, 2))} {e.currency} to {recipient_name}."
        elif e.entry_type == "TRANSFER_IN":
            sender_name = "another Sahara user"
            if e.reference and e.reference.startswith("S2S:"):
                try:
                    header = e.reference.split(":", 2)[1]
                    sender_part = header.split("->", 1)[0]
                    if "|" in sender_part:
                        # New format: S2S:<sender_name>|<sender_handle>->...
                        sender_name = sender_part.split("|", 1)[0].strip() or sender_name
                    else:
                        # Legacy format: S2S:<sender_handle>->...
                        sender_handle = sender_part.strip().lower()
                        if sender_handle.endswith("@sahara.com"):
                            sender_name = handle_to_name.get(sender_handle, sender_name)
                except Exception:
                    sender_name = "another Sahara user"
            summary = f"You received {round(e.amount, 2)} {e.currency} from {sender_name}."
        elif e.entry_type == "REFERRAL_BONUS":
            summary = f"Referral bonus: you earned {round(e.amount, 2)} {e.currency} when your invite reached the qualifying activity."
        events.append({"timestamp": e.created_at, "type": "wallet", "summary": summary})

    for w in withdrawals:
        events.append(
            {
                "timestamp": w.created_at,
                "type": "withdrawal",
                "summary": (
                    f"You sent {round(w.amount, 2)} {w.currency} to your {w.destination_type} "
                    f"account ending in {w.destination_account[-4:] if w.destination_account else '----'} "
                    f"(status: {w.status})."
                ),
            }
        )

    events.sort(key=lambda item: item["timestamp"], reverse=True)
    return events[:80]


@app.get("/admin/users")
def admin_users(_: User = Depends(require_admin), db: Session = Depends(get_db)):
    users = db.query(User).order_by(User.created_at.desc()).all()
    result = []
    for u in users:
        latest_kyc = (
            db.query(KYCSubmission)
            .filter(KYCSubmission.user_id == u.id)
            .order_by(KYCSubmission.created_at.desc())
            .first()
        )
        result.append(
            {
                "id": u.id,
                "email": u.email,
                "full_name": u.full_name,
                "kyc_status": u.kyc_status,
                "is_admin": u.is_admin,
                "passport_country": latest_kyc.passport_country if latest_kyc else None,
                "residence_country": latest_kyc.residence_country if latest_kyc else None,
                "current_city": latest_kyc.current_city if latest_kyc else None,
                "passport_number": latest_kyc.passport_number if latest_kyc else None,
                "latest_submission_id": latest_kyc.id if latest_kyc else None,
                "latest_submission_status": latest_kyc.status if latest_kyc else None,
                "profile_photo_url": get_latest_profile_photo(db, u.id),
                "password_visibility": "Not available (stored as secure hash only)",
                "created_at": u.created_at,
            }
        )
    return result


@app.patch("/admin/users/{user_id}/kyc")
def admin_update_kyc(
    user_id: int,
    status: str,
    notes: str = "",
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if status not in ("pending", "approved", "rejected"):
        raise HTTPException(status_code=400, detail="Invalid status")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.kyc_status = status
    latest_kyc = (
        db.query(KYCSubmission)
        .filter(KYCSubmission.user_id == user_id)
        .order_by(KYCSubmission.created_at.desc())
        .first()
    )
    if latest_kyc:
        if status == "pending":
            latest_kyc.status = "submitted"
            latest_kyc.reviewed_at = None
        else:
            latest_kyc.status = status
            latest_kyc.reviewed_at = datetime.utcnow()
        if notes:
            latest_kyc.reviewer_notes = notes
    db.commit()
    return {"message": "KYC updated"}


@app.get("/admin/withdrawals")
def admin_withdrawals(_: User = Depends(require_admin), db: Session = Depends(get_db)):
    rows = db.query(Withdrawal).order_by(Withdrawal.created_at.desc()).all()
    return [
        {
            "id": r.id,
            "user_id": r.user_id,
            "currency": r.currency,
            "amount": round(r.amount, 2),
            "destination_type": r.destination_type,
            "destination_account": r.destination_account,
            "status": r.status,
            "created_at": r.created_at,
        }
        for r in rows
    ]


@app.get("/admin/kyc-submissions")
def admin_kyc_submissions(_: User = Depends(require_admin), db: Session = Depends(get_db)):
    rows = db.query(KYCSubmission).order_by(KYCSubmission.created_at.desc()).all()
    return [
        {
            "id": r.id,
            "user_id": r.user_id,
            "passport_number": r.passport_number,
            "passport_country": r.passport_country,
            "residence_country": r.residence_country,
            "current_city": r.current_city,
            "passport_image_url": r.passport_image_url,
            "face_verification_image_url": r.face_verification_image_url or "",
            "status": r.status,
            "reviewer_notes": r.reviewer_notes,
            "created_at": r.created_at,
            "reviewed_at": r.reviewed_at,
        }
        for r in rows
    ]


@app.patch("/admin/kyc-submissions/{submission_id}")
def admin_review_kyc(
    submission_id: int,
    status: str,
    notes: str = "",
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if status not in ("approved", "rejected"):
        raise HTTPException(status_code=400, detail="status must be approved or rejected")
    row = db.query(KYCSubmission).filter(KYCSubmission.id == submission_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Submission not found")
    user = db.query(User).filter(User.id == row.user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    row.status = status
    row.reviewer_notes = notes
    row.reviewed_at = datetime.utcnow()
    user.kyc_status = status
    db.commit()
    return {"message": "KYC review updated", "submission_id": row.id, "status": row.status}


@app.get("/admin/stats")
def admin_stats(_: User = Depends(require_admin), db: Session = Depends(get_db)):
    total_users = db.query(func.count(User.id)).scalar() or 0
    pending_kyc = db.query(func.count(User.id)).filter(User.kyc_status.in_(["pending", "pending_review"])).scalar() or 0
    approved_kyc = db.query(func.count(User.id)).filter(User.kyc_status == "approved").scalar() or 0
    total_transactions = db.query(func.count(LedgerEntry.id)).scalar() or 0
    total_withdrawals = db.query(func.count(Withdrawal.id)).scalar() or 0
    total_usd_in = db.query(func.coalesce(func.sum(LedgerEntry.amount), 0.0)).filter(
        LedgerEntry.entry_type == "CREDIT_INBOUND", LedgerEntry.currency == "USD"
    ).scalar() or 0.0
    total_withdrawn_ghs = db.query(func.coalesce(func.sum(Withdrawal.amount), 0.0)).filter(
        Withdrawal.currency == "GHS"
    ).scalar() or 0.0
    processing_withdrawals = db.query(func.count(Withdrawal.id)).filter(Withdrawal.status == "processing").scalar() or 0

    return {
        "total_users": total_users,
        "pending_kyc": pending_kyc,
        "approved_kyc": approved_kyc,
        "total_transactions": total_transactions,
        "total_withdrawals": total_withdrawals,
        "processing_withdrawals": processing_withdrawals,
        "total_usd_in": round(float(total_usd_in), 2),
        "total_withdrawn_ghs": round(float(total_withdrawn_ghs), 2),
    }


@app.get("/admin/activity-feed")
def admin_activity_feed(_: User = Depends(require_admin), db: Session = Depends(get_db)):
    ledger = (
        db.query(LedgerEntry)
        .order_by(LedgerEntry.created_at.desc())
        .limit(30)
        .all()
    )
    kyc = (
        db.query(KYCSubmission)
        .order_by(KYCSubmission.created_at.desc())
        .limit(20)
        .all()
    )
    withdrawals = (
        db.query(Withdrawal)
        .order_by(Withdrawal.created_at.desc())
        .limit(20)
        .all()
    )

    events = []
    for l in ledger:
        events.append(
            {
                "timestamp": l.created_at,
                "type": "ledger",
                "user_id": l.user_id,
                "summary": f"{l.entry_type} {round(l.amount, 2)} {l.currency} ({l.reference})",
            }
        )
    for k in kyc:
        events.append(
            {
                "timestamp": k.created_at,
                "type": "kyc",
                "user_id": k.user_id,
                "summary": f"KYC {k.status} from {k.current_city}, {k.residence_country}",
            }
        )
    for w in withdrawals:
        events.append(
            {
                "timestamp": w.created_at,
                "type": "withdrawal",
                "user_id": w.user_id,
                "summary": f"Withdrawal {round(w.amount, 2)} {w.currency} to {w.destination_type}",
            }
        )

    events.sort(key=lambda item: item["timestamp"], reverse=True)
    return events[:50]


@app.get("/admin/users/{user_id}/activity")
def admin_user_activity(user_id: int, _: User = Depends(require_admin), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    ledger = (
        db.query(LedgerEntry)
        .filter(LedgerEntry.user_id == user_id)
        .order_by(LedgerEntry.created_at.desc())
        .limit(150)
        .all()
    )
    kyc = (
        db.query(KYCSubmission)
        .filter(KYCSubmission.user_id == user_id)
        .order_by(KYCSubmission.created_at.desc())
        .limit(50)
        .all()
    )
    withdrawals = (
        db.query(Withdrawal)
        .filter(Withdrawal.user_id == user_id)
        .order_by(Withdrawal.created_at.desc())
        .limit(50)
        .all()
    )
    logins = (
        db.query(LoginEvent)
        .filter(LoginEvent.user_id == user_id)
        .order_by(LoginEvent.created_at.desc())
        .limit(50)
        .all()
    )

    events = []
    for l in ledger:
        events.append(
            {
                "timestamp": l.created_at,
                "type": "ledger",
                "summary": f"{l.entry_type} {round(l.amount, 2)} {l.currency} ({l.reference})",
            }
        )
    for k in kyc:
        events.append(
            {
                "timestamp": k.created_at,
                "type": "kyc",
                "summary": f"KYC {k.status} from {k.current_city}, {k.residence_country}",
            }
        )
    for w in withdrawals:
        events.append(
            {
                "timestamp": w.created_at,
                "type": "withdrawal",
                "summary": f"Withdrawal {round(w.amount, 2)} {w.currency} to {w.destination_type} ({w.status})",
            }
        )
    for lg in logins:
        events.append(
            {
                "timestamp": lg.created_at,
                "type": "login",
                "summary": "User login",
            }
        )

    events.sort(key=lambda item: item["timestamp"], reverse=True)
    return {
        "user": {"id": user.id, "full_name": user.full_name, "email": user.email, "kyc_status": user.kyc_status},
        "events": events[:200],
    }


@app.get("/admin/analytics")
def admin_analytics(_: User = Depends(require_admin), db: Session = Depends(get_db)):
    login_rows = (
        db.query(func.date(LoginEvent.created_at).label("day"), func.count(LoginEvent.id).label("count"))
        .group_by(func.date(LoginEvent.created_at))
        .order_by(func.date(LoginEvent.created_at))
        .all()
    )
    tx_rows = (
        db.query(func.date(LedgerEntry.created_at).label("day"), func.count(LedgerEntry.id).label("count"))
        .group_by(func.date(LedgerEntry.created_at))
        .order_by(func.date(LedgerEntry.created_at))
        .all()
    )
    user_rows = (
        db.query(func.date(User.created_at).label("day"), func.count(User.id).label("count"))
        .group_by(func.date(User.created_at))
        .order_by(func.date(User.created_at))
        .all()
    )
    country_rows = (
        db.query(KYCSubmission.residence_country, func.count(KYCSubmission.id).label("count"))
        .group_by(KYCSubmission.residence_country)
        .order_by(func.count(KYCSubmission.id).desc())
        .all()
    )

    country_centroids = {
        "ghana": [7.9465, -1.0232],
        "united states": [37.0902, -95.7129],
        "nigeria": [9.0820, 8.6753],
        "kenya": [-0.0236, 37.9062],
        "south africa": [-30.5595, 22.9375],
        "uk": [55.3781, -3.4360],
        "united kingdom": [55.3781, -3.4360],
    }

    map_points = []
    for country, count in country_rows:
        key = (country or "").strip().lower()
        if key in country_centroids:
            lat, lng = country_centroids[key]
            map_points.append({"country": country, "count": int(count), "lat": lat, "lng": lng})

    return {
        "signins_by_day": [{"day": str(r.day), "count": int(r.count)} for r in login_rows],
        "transactions_by_day": [{"day": str(r.day), "count": int(r.count)} for r in tx_rows],
        "users_by_day": [{"day": str(r.day), "count": int(r.count)} for r in user_rows],
        "users_by_country": [{"country": r.residence_country or "Unknown", "count": int(r.count)} for r in country_rows],
        "map_points": map_points,
    }
