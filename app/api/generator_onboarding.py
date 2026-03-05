from datetime import datetime
import re

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import register_user, verify_token
from app.db.session import get_db
from app.models.models import (
    GeneratorInverterConnection,
    GeneratorProfile,
    Plant,
    User,
)
from app.schemas.generator_onboarding import (
    AddGeneratorConnectionRequest,
    GeneratorActivateRequest,
    GeneratorConnectionResponse,
    GeneratorOnboardingResponse,
    GeneratorRegisterRequest,
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

    user = db.query(User).filter(User.user_id == payload.get("user_id")).first()
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
