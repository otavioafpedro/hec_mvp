"""
Marketplace API for custody-backed inventory allocation.
"""
from uuid import UUID as UUIDType

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.auth import login_user, register_user, verify_token
from app.db.session import get_db
from app.marketplace import buy_from_lot
from app.models.models import HECLot, InventoryPosition, Transaction, User, Wallet
from app.schemas.marketplace_custody import (
    AuthResponse,
    BuyRequest,
    BuyResponse,
    LoginRequest,
    MarketplaceLotResponse,
    RegisterRequest,
    TransactionResponse,
    WalletResponse,
)

router = APIRouter(prefix="/marketplace", tags=["Marketplace"])


def get_current_user(
    authorization: str = Header(None, description="Token: Bearer <token>"),
    db: Session = Depends(get_db),
) -> User:
    if not authorization:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authorization header is required")

    token = authorization[7:] if authorization.startswith("Bearer ") else authorization
    payload = verify_token(token)
    if not payload:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")

    user_id = payload.get("user_id")
    try:
        user_uuid = UUIDType(str(user_id))
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token: malformed user_id")

    user = db.query(User).filter(User.user_id == user_uuid).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user


@router.post("/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
def register(req: RegisterRequest, db: Session = Depends(get_db)):
    try:
        user, wallet, token = register_user(
            db,
            email=req.email,
            name=req.name,
            password=req.password,
            wallet_address=req.wallet_address,
        )
        db.commit()
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))

    return AuthResponse(
        user_id=user.user_id,
        email=user.email,
        name=user.name,
        role=user.role,
        token=token,
        wallet_balance_brl=float(wallet.balance_brl),
        wallet_address=wallet.wallet_address,
        message=f"Conta criada. Saldo inicial da wallet: R$ {float(wallet.balance_brl):.2f}",
    )


@router.post("/login", response_model=AuthResponse)
def login(req: LoginRequest, db: Session = Depends(get_db)):
    try:
        user, token = login_user(db, email=req.email, password=req.password)
        db.commit()
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc))

    wallet = db.query(Wallet).filter(Wallet.user_id == user.user_id).first()
    return AuthResponse(
        user_id=user.user_id,
        email=user.email,
        name=user.name,
        role=user.role,
        token=token,
        wallet_balance_brl=float(wallet.balance_brl) if wallet else 0.0,
        wallet_address=wallet.wallet_address if wallet else None,
        message="Login realizado com sucesso",
    )


@router.get("/wallet", response_model=WalletResponse)
def get_wallet(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    wallet = db.query(Wallet).filter(Wallet.user_id == user.user_id).first()
    if not wallet:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Wallet not found")

    custody_hec, custody_energy = (
        db.query(
            func.coalesce(func.sum(InventoryPosition.available_quantity), 0),
            func.coalesce(func.sum(InventoryPosition.energy_kwh_available), 0),
        )
        .filter(
            InventoryPosition.wallet_id == wallet.wallet_id,
            InventoryPosition.available_quantity > 0,
        )
        .first()
    )

    return WalletResponse(
        wallet_id=wallet.wallet_id,
        user_id=wallet.user_id,
        wallet_address=wallet.wallet_address,
        balance_brl=float(wallet.balance_brl),
        hec_balance=wallet.hec_balance,
        energy_balance_kwh=float(wallet.energy_balance_kwh),
        custodied_hec_balance=int(custody_hec or 0),
        custodied_energy_balance_kwh=float(custody_energy or 0),
        message=(
            f"R$ {float(wallet.balance_brl):.2f} | "
            f"{int(custody_hec or 0)} HEC in custody | "
            f"{float(custody_energy or 0):.4f} kWh available"
        ),
    )


@router.get('/lots', response_model=list[MarketplaceLotResponse])
def list_marketplace_lots(db: Session = Depends(get_db)):
    lots = (
        db.query(HECLot)
        .filter(
            HECLot.status == "open",
            HECLot.available_quantity > 0,
            HECLot.price_per_kwh.isnot(None),
            HECLot.inventory_status == "issued",
            HECLot.onchain_issued_tx_hash.isnot(None),
        )
        .order_by(HECLot.created_at.desc())
        .all()
    )

    results = []
    for lot in lots:
        all_backed = bool(lot.onchain_issued_tx_hash) and all(
            cert.registry_tx_hash is not None and cert.ipfs_json_cid is not None
            for cert in (lot.certificates or [])
        )
        if not all_backed:
            continue

        total_price = None
        if lot.price_per_kwh and lot.total_energy_kwh:
            total_price = float(lot.price_per_kwh) * float(lot.total_energy_kwh)

        results.append(
            MarketplaceLotResponse(
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
                custody_mode=lot.custody_mode,
                transferability_policy=lot.transferability_policy,
                onchain_batch_token_id=lot.onchain_batch_token_id,
                certificate_count=lot.certificate_count,
                created_at=lot.created_at.isoformat() + 'Z',
            )
        )

    return results


@router.post('/buy', response_model=BuyResponse, status_code=status.HTTP_201_CREATED)
def buy_hecs(
    req: BuyRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        result = buy_from_lot(db=db, buyer_id=user.user_id, lot_id=req.lot_id, quantity=req.quantity)
        db.commit()
    except ValueError as exc:
        err = str(exc)
        if 'not found' in err:
            code = status.HTTP_404_NOT_FOUND
        elif 'not open' in err:
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
            f"Custody allocation completed: {result.quantity} HEC, "
            f"{result.energy_kwh:.4f} kWh, R$ {result.total_price_brl:.2f}"
        ),
    )


@router.get('/transactions', response_model=list[TransactionResponse])
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
            lot_name=tx.lot.name if tx.lot else '',
            quantity=tx.quantity,
            energy_kwh=float(tx.energy_kwh),
            unit_price_brl=float(tx.unit_price_brl),
            total_price_brl=float(tx.total_price_brl),
            status=tx.status,
            created_at=tx.created_at.isoformat() + 'Z',
        )
        for tx in txs
    ]
