"""
Marketplace API — Registro, login, carteira, lotes e compra de HECs.

POST /marketplace/register    — Criar conta + wallet
POST /marketplace/login       — Autenticar e obter token
GET  /marketplace/wallet      — Consultar saldo (autenticado)
GET  /marketplace/lots        — Listar lotes backed disponíveis
POST /marketplace/buy         — Comprar HECs de um lote (atômico)
GET  /marketplace/transactions — Histórico de transações do usuário
"""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Header, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.models import User, Wallet, HECLot, HECCertificate, Transaction
from app.schemas.marketplace import (
    RegisterRequest, LoginRequest, AuthResponse,
    WalletResponse, BuyRequest, BuyResponse,
    MarketplaceLotResponse, TransactionResponse,
)
from app.auth import register_user, login_user, verify_token
from app.marketplace import buy_from_lot

router = APIRouter(prefix="/marketplace", tags=["Marketplace"])


# ---------------------------------------------------------------------------
# Auth dependency
# ---------------------------------------------------------------------------

def get_current_user(
    authorization: str = Header(None, description="Token: Bearer <token>"),
    db: Session = Depends(get_db),
) -> User:
    """Extract and validate user from Authorization header."""
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header obrigatório",
        )

    token = authorization
    if token.startswith("Bearer "):
        token = token[7:]

    payload = verify_token(token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido ou expirado",
        )

    user_id = payload.get("user_id")
    user = db.query(User).filter(User.user_id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuário não encontrado",
        )

    return user


# ---------------------------------------------------------------------------
# POST /register
# ---------------------------------------------------------------------------

@router.post(
    "/register",
    response_model=AuthResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Criar conta no marketplace",
)
def register(req: RegisterRequest, db: Session = Depends(get_db)):
    try:
        user, wallet, token = register_user(
            db, email=req.email, name=req.name, password=req.password,
        )
        db.commit()
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e),
        )

    return AuthResponse(
        user_id=user.user_id,
        email=user.email,
        name=user.name,
        role=user.role,
        token=token,
        wallet_balance_brl=float(wallet.balance_brl),
        message=f"Conta criada — saldo inicial R$ {float(wallet.balance_brl):.2f}",
    )


# ---------------------------------------------------------------------------
# POST /login
# ---------------------------------------------------------------------------

@router.post(
    "/login",
    response_model=AuthResponse,
    summary="Autenticar no marketplace",
)
def login(req: LoginRequest, db: Session = Depends(get_db)):
    try:
        user, token = login_user(db, email=req.email, password=req.password)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e),
        )

    wallet = db.query(Wallet).filter(Wallet.user_id == user.user_id).first()
    balance = float(wallet.balance_brl) if wallet else 0

    return AuthResponse(
        user_id=user.user_id,
        email=user.email,
        name=user.name,
        role=user.role,
        token=token,
        wallet_balance_brl=balance,
        message="Login realizado com sucesso",
    )


# ---------------------------------------------------------------------------
# GET /wallet
# ---------------------------------------------------------------------------

@router.get(
    "/wallet",
    response_model=WalletResponse,
    summary="Consultar carteira (autenticado)",
)
def get_wallet(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    wallet = db.query(Wallet).filter(Wallet.user_id == user.user_id).first()
    if not wallet:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Wallet não encontrada",
        )

    return WalletResponse(
        wallet_id=wallet.wallet_id,
        user_id=wallet.user_id,
        balance_brl=float(wallet.balance_brl),
        hec_balance=wallet.hec_balance,
        energy_balance_kwh=float(wallet.energy_balance_kwh),
        message=f"R$ {float(wallet.balance_brl):.2f} | {wallet.hec_balance} HECs | {float(wallet.energy_balance_kwh):.4f} kWh",
    )


# ---------------------------------------------------------------------------
# GET /lots (somente backed)
# ---------------------------------------------------------------------------

@router.get(
    "/lots",
    response_model=list[MarketplaceLotResponse],
    summary="Listar lotes backed disponíveis no marketplace",
)
def list_marketplace_lots(db: Session = Depends(get_db)):
    """
    Lista SOMENTE lotes com:
      - status = "open"
      - available_quantity > 0
      - price_per_kwh definido
      - Todos os HECs com backing completo
    """
    lots = (
        db.query(HECLot)
        .filter(
            HECLot.status == "open",
            HECLot.available_quantity > 0,
            HECLot.price_per_kwh.isnot(None),
        )
        .order_by(HECLot.created_at.desc())
        .all()
    )

    results = []
    for lot in lots:
        # Verify backing complete
        all_backed = all(
            c.registry_tx_hash is not None and c.ipfs_json_cid is not None
            for c in (lot.certificates or [])
        )
        if not all_backed:
            continue  # Skip lots without full backing

        total_price = None
        if lot.price_per_kwh and lot.total_energy_kwh:
            total_price = float(lot.price_per_kwh) * float(lot.total_energy_kwh)

        results.append(MarketplaceLotResponse(
            lot_id=lot.lot_id,
            name=lot.name,
            description=lot.description,
            total_quantity=lot.total_quantity,
            available_quantity=lot.available_quantity,
            total_energy_kwh=float(lot.total_energy_kwh),
            price_per_kwh=float(lot.price_per_kwh) if lot.price_per_kwh else None,
            total_price_brl=total_price,
            status=lot.status,
            backing_complete=True,
            certificate_count=lot.total_quantity,
            created_at=lot.created_at.isoformat() + "Z",
        ))

    return results


# ---------------------------------------------------------------------------
# POST /buy
# ---------------------------------------------------------------------------

@router.post(
    "/buy",
    response_model=BuyResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Comprar HECs de um lote (transação atômica)",
    description=(
        "Compra quantity HECs do lote. "
        "Transação atômica: debita saldo, credita HECs, decrementa lote. "
        "Não pode comprar mais que available_quantity."
    ),
)
def buy_hecs(
    req: BuyRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        result = buy_from_lot(
            db=db,
            buyer_id=user.user_id,
            lot_id=req.lot_id,
            quantity=req.quantity,
        )
        db.commit()
    except ValueError as e:
        err = str(e)
        if "não encontrado" in err:
            code = status.HTTP_404_NOT_FOUND
        elif "excede disponível" in err or "Saldo insuficiente" in err:
            code = status.HTTP_422_UNPROCESSABLE_ENTITY
        elif "não está aberto" in err:
            code = status.HTTP_409_CONFLICT
        else:
            code = status.HTTP_422_UNPROCESSABLE_ENTITY
        raise HTTPException(status_code=code, detail=err)

    return BuyResponse(
        tx_id=result.tx_id,
        buyer_id=result.buyer_id,
        lot_id=result.lot_id,
        quantity=result.quantity,
        energy_kwh=result.energy_kwh,
        unit_price_brl=result.unit_price_brl,
        total_price_brl=result.total_price_brl,
        wallet_balance_after=result.wallet_balance_after,
        wallet_hec_after=result.wallet_hec_after,
        wallet_energy_after=result.wallet_energy_after,
        lot_available_after=result.lot_available_after,
        lot_status_after=result.lot_status_after,
        status=result.status,
        message=(
            f"Compra realizada — {result.quantity} HECs, "
            f"{result.energy_kwh:.4f} kWh, "
            f"R$ {result.total_price_brl:.2f}"
        ),
    )


# ---------------------------------------------------------------------------
# GET /transactions
# ---------------------------------------------------------------------------

@router.get(
    "/transactions",
    response_model=list[TransactionResponse],
    summary="Histórico de transações do usuário",
)
def list_transactions(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    txs = (
        db.query(Transaction)
        .filter(Transaction.buyer_id == user.user_id)
        .order_by(Transaction.created_at.desc())
        .all()
    )

    return [
        TransactionResponse(
            tx_id=tx.tx_id,
            lot_id=tx.lot_id,
            lot_name=tx.lot.name if tx.lot else "",
            quantity=tx.quantity,
            energy_kwh=float(tx.energy_kwh),
            unit_price_brl=float(tx.unit_price_brl),
            total_price_brl=float(tx.total_price_brl),
            status=tx.status,
            created_at=tx.created_at.isoformat() + "Z",
        )
        for tx in txs
    ]
