"""Pydantic schemas for marketplace and custody ledger views."""
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


class RegisterRequest(BaseModel):
    email: str = Field(..., min_length=5, description="Email do usuario")
    name: str = Field(..., min_length=2, max_length=255)
    password: str = Field(..., min_length=6, description="Senha (min 6 chars)")
    wallet_address: Optional[str] = Field(
        None,
        description="Endereco EVM opcional do titular economico",
    )


class LoginRequest(BaseModel):
    email: str
    password: str


class AuthResponse(BaseModel):
    user_id: UUID
    email: str
    name: str
    role: str
    token: str
    wallet_balance_brl: float = 0
    wallet_address: Optional[str] = None
    message: str = ""


class WalletResponse(BaseModel):
    wallet_id: UUID
    user_id: UUID
    wallet_address: Optional[str] = None
    balance_brl: float
    hec_balance: int
    energy_balance_kwh: float
    custodied_hec_balance: int = 0
    custodied_energy_balance_kwh: float = 0.0
    message: str = ""


class BuyRequest(BaseModel):
    lot_id: UUID = Field(..., description="ID do lote a comprar")
    quantity: int = Field(..., ge=1, description="Quantidade de HECs")


class BuyResponse(BaseModel):
    tx_id: UUID
    buyer_id: UUID
    lot_id: UUID
    quantity: int
    energy_kwh: float
    unit_price_brl: float
    total_price_brl: float
    wallet_balance_after: float
    wallet_hec_after: int
    wallet_energy_after: float
    lot_available_after: int
    lot_status_after: str
    status: str = "completed"
    message: str = ""


class MarketplaceLotResponse(BaseModel):
    lot_id: UUID
    name: str
    description: Optional[str] = None
    total_quantity: int
    available_quantity: int
    total_energy_kwh: float
    price_per_kwh: Optional[float] = None
    total_price_brl: Optional[float] = None
    status: str
    backing_complete: bool
    custody_mode: str = "platform_custody"
    transferability_policy: str = "non_transferable"
    onchain_batch_token_id: Optional[int] = None
    certificate_count: int = 0
    created_at: str


class TransactionResponse(BaseModel):
    tx_id: UUID
    lot_id: UUID
    lot_name: str = ""
    quantity: int
    energy_kwh: float
    unit_price_brl: float
    total_price_brl: float
    status: str
    created_at: str
