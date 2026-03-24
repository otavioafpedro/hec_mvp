from datetime import datetime, timedelta
from decimal import Decimal
import re
from uuid import UUID as UUIDType

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy import and_, case, func
from sqlalchemy.orm import Session

from app.auth import register_user, verify_token
from app.db.session import get_db
from app.identity import ensure_consumer_identity
from app.models.models import (
    GeneratorInverterConnection,
    GeneratorProfile,
    HECCertificate,
    HECLot,
    Plant,
    Transaction,
    User,
    Validation,
)
from app.schemas.generator_onboarding import (
    AddGeneratorConnectionRequest,
    GeneratorActivateRequest,
    GeneratorConnectionResponse,
    GeneratorOnboardingResponse,
    GeneratorRegisterRequest,
)
from app.schemas.generator_supplier_dashboard import (
    GeneratorSupplierDashboardResponse,
    SupplierHourlyGenerationItem,
    SupplierMintCandidateItem,
    SupplierPlantDashboardItem,
    SupplierQSVStatus,
    SupplierRecentHecItem,
)

router = APIRouter(prefix="/generator-onboarding", tags=["Generator Onboarding"])


def _normalize_document(document_id: str) -> str:
    cleaned = re.sub(r"\D+", "", document_id or "")
    return cleaned or (document_id or "").strip()


def _to_connection_response(conn: GeneratorInverterConnection) -> GeneratorConnectionResponse:
    return GeneratorConnectionResponse(
        connection_id=conn.connection_id,
        profile_id=conn.profile_id,
        plant_id=conn.plant_id,
        provider_name=conn.provider_name,
        integration_mode=conn.integration_mode,
        external_account_ref=conn.external_account_ref,
        inverter_serial=conn.inverter_serial,
        consent_accepted=bool(conn.consent_accepted),
        consented_at=conn.consented_at,
        connection_status=conn.connection_status,
        last_sync_at=conn.last_sync_at,
        created_at=conn.created_at,
    )


def _build_response(
    user: User,
    profile: GeneratorProfile,
    plant: Plant,
    connections: list[GeneratorInverterConnection],
    token: str | None = None,
    message: str = "",
) -> GeneratorOnboardingResponse:
    return GeneratorOnboardingResponse(
        user_id=user.user_id,
        profile_id=profile.profile_id,
        email=user.email,
        name=user.name,
        role=user.role,
        person_type=profile.person_type,
        document_id=profile.document_id,
        legal_name=profile.legal_name,
        trade_name=profile.trade_name,
        phone=profile.phone,
        onboarding_status=profile.onboarding_status,
        attribute_assignment_accepted=bool(profile.attribute_assignment_accepted),
        assignment_accepted_at=profile.assignment_accepted_at,
        plant_id=plant.plant_id,
        plant_name=plant.name,
        token=token,
        connections=[_to_connection_response(conn) for conn in connections],
        message=message,
    )


def _as_float(value: float | int | Decimal | None) -> float:
    if value is None:
        return 0.0
    return float(value)


def _kwh_to_mwh(value: float | int | Decimal | None) -> float:
    return _as_float(value) / 1000.0


def _format_capacity(capacity_kw: float | int | Decimal | None) -> str:
    kw = _as_float(capacity_kw)
    if kw >= 1000:
        return f"{kw / 1000:.1f} MW"
    return f"{kw:.0f} kW"


def _infer_plant_type(name: str | None) -> str:
    normalized = (name or "").strip().lower()
    if any(token in normalized for token in ("eolica", "eÃ³lica", "wind")):
        return "Eolica"
    if any(token in normalized for token in ("solar", "fotovoltaica", "fotovoltaico", "pv")):
        return "Solar"
    return "Solar"


def _normalize_dashboard_status(status_value: str | None) -> str:
    normalized = (status_value or "").strip().lower()
    if normalized in {"active", "online"}:
        return "online"
    return "maintenance"


def _format_location(lat: float | int | Decimal | None, lng: float | int | Decimal | None) -> str:
    lat_value = _as_float(lat)
    lng_value = _as_float(lng)
    return f"{lat_value:.4f}, {lng_value:.4f}"


def _format_brl(value: float | int | Decimal | None) -> str:
    amount = _as_float(value)
    formatted = f"{amount:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {formatted}"


def _format_last_sync(sync_dt: datetime | None) -> str:
    if not sync_dt:
        return "-"
    delta = datetime.utcnow() - sync_dt
    if delta.total_seconds() < 60:
        return f"ha {int(delta.total_seconds())}s"
    if delta.total_seconds() < 3600:
        return f"ha {int(delta.total_seconds() // 60)}m"
    return f"ha {int(delta.total_seconds() // 3600)}h"


def _tier_from_confidence(score: float | int | Decimal | None) -> str:
    value = _as_float(score)
    if value >= 95:
        return "Tier 1"
    if value >= 85:
        return "Tier 2"
    return "Tier 3"


def _create_onboarding_entities(
    db: Session,
    user: User,
    payload: GeneratorRegisterRequest | GeneratorActivateRequest,
):
    document_id = _normalize_document(payload.document_id)
    existing_doc = (
        db.query(GeneratorProfile)
        .filter(GeneratorProfile.document_id == document_id)
        .first()
    )
    if existing_doc and existing_doc.user_id != user.user_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Documento {document_id} ja cadastrado para outro gerador",
        )

    now = datetime.utcnow()
    plant = Plant(
        name=payload.plant.name,
        absolar_id=payload.plant.absolar_id,
        owner_name=payload.legal_name or user.name,
        owner_user_id=user.user_id,
        lat=payload.plant.lat,
        lng=payload.plant.lng,
        capacity_kw=payload.plant.capacity_kw,
        status="pending",
        inverter_brand=payload.plant.inverter_brand,
        inverter_model=payload.plant.inverter_model,
    )
    db.add(plant)
    db.flush()

    profile = GeneratorProfile(
        user_id=user.user_id,
        person_type=payload.person_type,
        document_id=document_id,
        legal_name=payload.legal_name,
        trade_name=payload.trade_name,
        phone=payload.phone,
        attribute_assignment_accepted=True,
        assignment_accepted_at=now,
        onboarding_status="integration_pending",
    )
    db.add(profile)
    db.flush()

    connection = GeneratorInverterConnection(
        profile_id=profile.profile_id,
        plant_id=plant.plant_id,
        provider_name=payload.inverter_connection.provider_name,
        integration_mode=payload.inverter_connection.integration_mode,
        external_account_ref=payload.inverter_connection.external_account_ref,
        inverter_serial=payload.inverter_connection.inverter_serial,
        consent_accepted=True,
        consented_at=now,
        connection_status="pending",
    )
    db.add(connection)
    return profile, plant, connection


def get_current_user(
    authorization: str = Header(None, description="Token: Bearer <token>"),
    db: Session = Depends(get_db),
) -> User:
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header obrigatorio",
        )

    token = authorization[7:] if authorization.startswith("Bearer ") else authorization
    payload = verify_token(token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token invalido ou expirado",
        )

    user_id = payload.get("user_id")
    try:
        user_uuid = UUIDType(str(user_id))
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token invalido: user_id malformado",
        )

    user = db.query(User).filter(User.user_id == user_uuid).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuario nao encontrado",
        )
    return user


@router.post(
    "/register",
    response_model=GeneratorOnboardingResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Cadastrar gerador (PF/PJ) + iniciar onboarding de inversor",
)
def register_generator(req: GeneratorRegisterRequest, db: Session = Depends(get_db)):
    try:
        user, _, token = register_user(
            db=db,
            email=req.email,
            name=req.name,
            password=req.password,
            role="seller",
        )
        profile, plant, connection = _create_onboarding_entities(db=db, user=user, payload=req)
        ensure_consumer_identity(db, user)

        db.commit()
        db.refresh(user)
        db.refresh(profile)
        db.refresh(plant)
        db.refresh(connection)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        )
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Falha de integridade ao cadastrar gerador (email/documento/identificador duplicado)",
        )

    return _build_response(
        user=user,
        profile=profile,
        plant=plant,
        connections=[connection],
        token=token,
        message="Gerador cadastrado com sucesso. Onboarding de inversor iniciado.",
    )


@router.post(
    "/activate",
    response_model=GeneratorOnboardingResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Ativar perfil de gerador para usuario autenticado (conta consumidor -> conta hibrida)",
)
def activate_generator_profile(
    req: GeneratorActivateRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    existing_profile = db.query(GeneratorProfile).filter(GeneratorProfile.user_id == user.user_id).first()
    if existing_profile:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Usuario ja possui perfil de gerador ativo",
        )

    try:
        profile, plant, connection = _create_onboarding_entities(db=db, user=user, payload=req)
        ensure_consumer_identity(db, user)
        db.commit()
        db.refresh(profile)
        db.refresh(plant)
        db.refresh(connection)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Falha de integridade ao ativar perfil de gerador",
        )

    return _build_response(
        user=user,
        profile=profile,
        plant=plant,
        connections=[connection],
        message="Perfil de gerador ativado na conta existente.",
    )


@router.get(
    "/me",
    response_model=GeneratorOnboardingResponse,
    summary="Consultar onboarding do gerador autenticado",
)
def get_my_onboarding(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    profile = db.query(GeneratorProfile).filter(GeneratorProfile.user_id == user.user_id).first()
    if not profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Perfil de gerador nao encontrado para este usuario",
        )

    connections = (
        db.query(GeneratorInverterConnection)
        .filter(GeneratorInverterConnection.profile_id == profile.profile_id)
        .order_by(GeneratorInverterConnection.created_at.desc())
        .all()
    )
    plant_id = connections[0].plant_id if connections else None
    plant = None
    if plant_id:
        plant = db.query(Plant).filter(Plant.plant_id == plant_id).first()
    if not plant:
        plant = (
            db.query(Plant)
            .filter(Plant.owner_user_id == user.user_id)
            .order_by(Plant.created_at.desc())
            .first()
        )
    if not plant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Usina do gerador nao encontrada",
        )

    return _build_response(
        user=user,
        profile=profile,
        plant=plant,
        connections=connections,
        message="Onboarding carregado",
    )


@router.post(
    "/connections",
    response_model=GeneratorConnectionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Adicionar nova conexao de inversor para o gerador autenticado",
)
def add_generator_connection(
    req: AddGeneratorConnectionRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    profile = db.query(GeneratorProfile).filter(GeneratorProfile.user_id == user.user_id).first()
    if not profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Perfil de gerador nao encontrado para este usuario",
        )

    plant = None
    if req.plant_id:
        plant = db.query(Plant).filter(Plant.plant_id == req.plant_id).first()
        if not plant:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Planta {req.plant_id} nao encontrada",
            )
        if plant.owner_user_id != user.user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="A planta informada nao pertence ao usuario autenticado",
            )
    else:
        plant = (
            db.query(Plant)
            .filter(Plant.owner_user_id == user.user_id)
            .order_by(Plant.created_at.desc())
            .first()
        )

    if not req.consent_accepted:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="consent_accepted deve ser true para adicionar conexao",
        )

    connection = GeneratorInverterConnection(
        profile_id=profile.profile_id,
        plant_id=plant.plant_id if plant else None,
        provider_name=req.provider_name,
        integration_mode=req.integration_mode,
        external_account_ref=req.external_account_ref,
        inverter_serial=req.inverter_serial,
        consent_accepted=True,
        consented_at=datetime.utcnow(),
        connection_status="pending",
    )
    db.add(connection)
    db.commit()
    db.refresh(connection)

    if profile.onboarding_status == "draft":
        profile.onboarding_status = "integration_pending"
        db.commit()

    return _to_connection_response(connection)


@router.get(
    "/supplier-dashboard",
    response_model=GeneratorSupplierDashboardResponse,
    summary="Dashboard de fornecedor do gerador autenticado",
)
def get_supplier_dashboard(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    profile = db.query(GeneratorProfile).filter(GeneratorProfile.user_id == user.user_id).first()
    if not profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Perfil de gerador nao encontrado para este usuario",
        )

    plants = (
        db.query(Plant)
        .filter(Plant.owner_user_id == user.user_id)
        .order_by(Plant.created_at.asc())
        .all()
    )
    if not plants:
        return GeneratorSupplierDashboardResponse(
            profile_status=profile.onboarding_status,
            split_percentage=70,
            plants=[],
            recent_hecs=[],
            hourly_generation=[SupplierHourlyGenerationItem(hour=f"{h:02d}:00", solar=0.0, wind=0.0) for h in range(24)],
            mint_candidates=[],
            generated_at=datetime.utcnow(),
        )

    plant_ids = [p.plant_id for p in plants]
    now = datetime.utcnow()
    day_start = datetime(now.year, now.month, now.day)
    day_end = day_start + timedelta(days=1)
    month_start = datetime(now.year, now.month, 1)

    generation_rows = (
        db.query(
            Validation.plant_id.label("plant_id"),
            func.sum(
                case(
                    (
                        and_(
                            Validation.period_end >= day_start,
                            Validation.period_end < day_end,
                            Validation.status != "rejected",
                        ),
                        Validation.energy_kwh,
                    ),
                    else_=0,
                )
            ).label("gen_today_kwh"),
            func.sum(
                case(
                    (
                        and_(
                            Validation.period_end >= month_start,
                            Validation.status != "rejected",
                        ),
                        Validation.energy_kwh,
                    ),
                    else_=0,
                )
            ).label("gen_month_kwh"),
            func.count(Validation.validation_id).label("validation_count"),
            func.sum(case((Validation.status == "approved", 1), else_=0)).label("approved_count"),
        )
        .filter(Validation.plant_id.in_(plant_ids))
        .group_by(Validation.plant_id)
        .all()
    )
    generation_by_plant = {row.plant_id: row for row in generation_rows}

    hec_rows = (
        db.query(
            Validation.plant_id.label("plant_id"),
            func.count(HECCertificate.hec_id).label("hecs_emitted"),
            func.sum(
                case(
                    (HECCertificate.status.in_(("registered", "minted", "custodied")), 1),
                    else_=0,
                )
            ).label("hecs_available"),
            func.sum(
                case(
                    (
                        HECCertificate.status.in_(("allocated", "retired")),
                        HECCertificate.energy_kwh * func.coalesce(HECLot.price_per_kwh, 0),
                    ),
                    else_=0,
                )
            ).label("gross_revenue_brl"),
        )
        .join(Validation, Validation.validation_id == HECCertificate.validation_id)
        .outerjoin(HECLot, HECLot.lot_id == HECCertificate.lot_id)
        .filter(Validation.plant_id.in_(plant_ids))
        .group_by(Validation.plant_id)
        .all()
    )
    hec_by_plant = {row.plant_id: row for row in hec_rows}

    latest_validation_subquery = (
        db.query(
            Validation.plant_id.label("plant_id"),
            func.max(Validation.period_end).label("latest_period_end"),
        )
        .filter(Validation.plant_id.in_(plant_ids))
        .group_by(Validation.plant_id)
        .subquery()
    )

    latest_validations = (
        db.query(Validation)
        .join(
            latest_validation_subquery,
            and_(
                Validation.plant_id == latest_validation_subquery.c.plant_id,
                Validation.period_end == latest_validation_subquery.c.latest_period_end,
            ),
        )
        .all()
    )
    latest_validation_by_plant: dict = {}
    for item in latest_validations:
        current = latest_validation_by_plant.get(item.plant_id)
        if current is None or (item.period_end and current.period_end and item.period_end > current.period_end):
            latest_validation_by_plant[item.plant_id] = item

    sync_rows = (
        db.query(
            GeneratorInverterConnection.plant_id.label("plant_id"),
            func.max(GeneratorInverterConnection.last_sync_at).label("last_sync_at"),
        )
        .filter(GeneratorInverterConnection.plant_id.in_(plant_ids))
        .group_by(GeneratorInverterConnection.plant_id)
        .all()
    )
    sync_by_plant = {row.plant_id: row.last_sync_at for row in sync_rows}

    plants_payload: list[SupplierPlantDashboardItem] = []
    for plant in plants:
        generation = generation_by_plant.get(plant.plant_id)
        hec_stats = hec_by_plant.get(plant.plant_id)
        latest = latest_validation_by_plant.get(plant.plant_id)

        validation_count = int(getattr(generation, "validation_count", 0) or 0)
        approved_count = int(getattr(generation, "approved_count", 0) or 0)
        efficiency = round((approved_count / validation_count) * 100.0, 1) if validation_count > 0 else 0.0

        qsv = SupplierQSVStatus(
            ccee=bool(latest.status == "approved") if latest else True,
            mqtt=bool(latest.ntp_pass) if latest and latest.ntp_pass is not None else True,
            meter=bool(latest.physics_pass) if latest and latest.physics_pass is not None else True,
            sat=bool(latest.satellite_pass) if latest and latest.satellite_pass is not None else True,
        )
        last_sync = sync_by_plant.get(plant.plant_id) or (latest.period_end if latest else None) or plant.updated_at

        plants_payload.append(
            SupplierPlantDashboardItem(
                id=plant.plant_id,
                name=plant.name,
                type=_infer_plant_type(plant.name),
                capacity=_format_capacity(plant.capacity_kw),
                location=_format_location(plant.lat, plant.lng),
                status=_normalize_dashboard_status(plant.status),
                genToday=round(_kwh_to_mwh(getattr(generation, "gen_today_kwh", 0)), 2),
                genMonth=round(_kwh_to_mwh(getattr(generation, "gen_month_kwh", 0)), 2),
                efficiency=efficiency,
                hecsEmitted=int(getattr(hec_stats, "hecs_emitted", 0) or 0),
                hecsAvailable=int(getattr(hec_stats, "hecs_available", 0) or 0),
                revenue=round(_as_float(getattr(hec_stats, "gross_revenue_brl", 0)) * 0.7, 2),
                qsv=qsv,
                lastSync=_format_last_sync(last_sync),
            )
        )

    recent_rows = (
        db.query(HECCertificate, Validation, Plant, HECLot)
        .join(Validation, Validation.validation_id == HECCertificate.validation_id)
        .join(Plant, Plant.plant_id == Validation.plant_id)
        .outerjoin(HECLot, HECLot.lot_id == HECCertificate.lot_id)
        .filter(Validation.plant_id.in_(plant_ids))
        .order_by(HECCertificate.minted_at.desc())
        .limit(30)
        .all()
    )

    lot_ids = [lot.lot_id for _, _, _, lot in recent_rows if lot and lot.lot_id]
    buyer_by_lot = {}
    if lot_ids:
        latest_tx_subquery = (
            db.query(
                Transaction.lot_id.label("lot_id"),
                func.max(Transaction.created_at).label("latest_tx_at"),
            )
            .filter(Transaction.lot_id.in_(lot_ids))
            .group_by(Transaction.lot_id)
            .subquery()
        )
        latest_buyers = (
            db.query(Transaction.lot_id, User.name)
            .join(
                latest_tx_subquery,
                and_(
                    Transaction.lot_id == latest_tx_subquery.c.lot_id,
                    Transaction.created_at == latest_tx_subquery.c.latest_tx_at,
                ),
            )
            .join(User, User.user_id == Transaction.buyer_id)
            .all()
        )
        buyer_by_lot = {row.lot_id: row.name for row in latest_buyers}

    recent_payload: list[SupplierRecentHecItem] = []
    for hec, validation, plant, lot in recent_rows:
        total_price_brl = 0.0
        if lot and lot.price_per_kwh:
            total_price_brl = _as_float(hec.energy_kwh) * _as_float(lot.price_per_kwh)
        status_value = "minted" if hec.status in ("allocated", "retired", "custodied", "minted", "registered") else "available"
        buyer_name = "-"
        if hec.lot_id in buyer_by_lot:
            buyer_name = buyer_by_lot[hec.lot_id]
        hour_label = "-"
        if validation and validation.period_start and validation.period_end:
            hour_label = f"{validation.period_start.strftime('%H:%M')}-{validation.period_end.strftime('%H:%M')}"
        merkle = "-"
        if hec.hash_sha256:
            merkle = f"0x{hec.hash_sha256[:4]}...{hec.hash_sha256[-4:]}"

        recent_payload.append(
            SupplierRecentHecItem(
                id=f"HEC-{str(hec.hec_id)[:8].upper()}",
                plant=plant.name,
                hour=hour_label,
                mwh=round(_kwh_to_mwh(hec.energy_kwh), 3),
                price=_format_brl(total_price_brl),
                status=status_value,
                merkle=merkle,
                buyer=buyer_name,
                tier=_tier_from_confidence(validation.confidence_score if validation else None),
            )
        )

    hourly_bucket = {h: {"solar": 0.0, "wind": 0.0} for h in range(24)}
    hourly_rows = (
        db.query(Validation.period_start, Validation.energy_kwh, Plant.name)
        .join(Plant, Plant.plant_id == Validation.plant_id)
        .filter(
            Validation.plant_id.in_(plant_ids),
            Validation.period_start >= day_start,
            Validation.period_start < day_end,
            Validation.status != "rejected",
        )
        .all()
    )

    for period_start, energy_kwh, plant_name in hourly_rows:
        if not period_start:
            continue
        idx = int(period_start.hour)
        if idx < 0 or idx > 23:
            continue
        mwh = _kwh_to_mwh(energy_kwh)
        if _infer_plant_type(plant_name) == "Eolica":
            hourly_bucket[idx]["wind"] += mwh
        else:
            hourly_bucket[idx]["solar"] += mwh

    hourly_payload = [
        SupplierHourlyGenerationItem(
            hour=f"{h:02d}:00",
            solar=round(hourly_bucket[h]["solar"], 3),
            wind=round(hourly_bucket[h]["wind"], 3),
        )
        for h in range(24)
    ]

    mint_candidate_rows = (
        db.query(Validation, Plant)
        .join(Plant, Plant.plant_id == Validation.plant_id)
        .outerjoin(HECCertificate, HECCertificate.validation_id == Validation.validation_id)
        .filter(
            Validation.plant_id.in_(plant_ids),
            Validation.status == "approved",
            HECCertificate.hec_id.is_(None),
        )
        .order_by(Validation.period_end.desc(), Validation.period_start.desc())
        .limit(50)
        .all()
    )

    mint_candidates_payload = [
        SupplierMintCandidateItem(
            validation_id=validation.validation_id,
            plant_id=plant.plant_id,
            plant=plant.name,
            period_start=validation.period_start,
            period_end=validation.period_end,
            energy_kwh=round(_as_float(validation.energy_kwh), 4),
            confidence_score=(
                round(_as_float(validation.confidence_score), 2)
                if validation.confidence_score is not None
                else None
            ),
        )
        for validation, plant in mint_candidate_rows
    ]

    return GeneratorSupplierDashboardResponse(
        profile_status=profile.onboarding_status,
        split_percentage=70,
        plants=plants_payload,
        recent_hecs=recent_payload,
        hourly_generation=hourly_payload,
        mint_candidates=mint_candidates_payload,
        generated_at=datetime.utcnow(),
    )

