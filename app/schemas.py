from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, EmailStr


class RegisterRequest(BaseModel):
    email: EmailStr
    full_name: str
    password: str
    referral_code: Optional[str] = None  # demo: friend's code from ?ref= or invite


class LoginRequest(BaseModel):
    email: str
    password: str
    otp: Optional[str] = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class WalletResponse(BaseModel):
    currency: str
    balance: float


class ProfileResponse(BaseModel):
    id: int
    email: EmailStr
    full_name: str
    is_admin: bool = False
    kyc_status: str
    profile_photo_url: Optional[str] = None
    public_id: Optional[str] = None
    pay_handle: Optional[str] = None
    wallets: List[WalletResponse]


class AddFundsRequest(BaseModel):
    amount_usd: float
    reference: str


class FXQuoteRequest(BaseModel):
    amount_usd: float


class FXQuoteResponse(BaseModel):
    quote_id: int
    rate: float
    fee: float
    target_amount_ghs: float
    expires_note: str = "MVP quote; no expiry enforcement yet."


class ConvertRequest(BaseModel):
    quote_id: int


class FXConvertDirectRequest(BaseModel):
    amount_usd: float
    to_currency: str = "GHS"  # GHS or NGN


class WithdrawRequest(BaseModel):
    currency: str
    amount: float
    destination_type: str
    destination_account: str


class WithdrawalResponse(BaseModel):
    withdrawal_id: int
    status: str
    currency: str
    amount: float
    destination_type: str
    created_at: datetime


class KYCSubmissionRequest(BaseModel):
    passport_number: str
    passport_country: str
    residence_country: str
    current_city: str
    passport_image_url: str
    face_verification_image_url: str


class AddPaymentMethodRequest(BaseModel):
    method_type: str  # card or bank
    provider: str
    holder_name: str
    account_number: str
