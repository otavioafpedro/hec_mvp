from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID as UUIDType, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import and_, desc, or_
from sqlalchemy.orm import Session

from app.auth import verify_token
from app.db.session import get_db
from app.identity import (
    avatar_seed_from_name,
    ensure_consumer_identity,
    ensure_user_role_bindings,
    infer_default_person_type,
)
from app.models.models import (
    AchievementCatalog,
    BurnCertificate,
    ConsumerDashboardSnapshot,
    ConsumerProfile,
    ConsumerRewardLedger,
    DNFTDefinition,
    User,
    UserAchievement,
    UserDNFTEvent,
    UserDNFTState,
    UserRoleBinding,
)
from app.schemas.consumer_pf import (
    AchievementItem,
    ConsumerPFDashboardResponse,
    ConsumerProfileUpsertRequest,
    DNFTSummary,
    DNFTTierHistoryItem,
    LeaderboardItem,
    MonthlyFootprintItem,
    PFUserSummary,
    SimulateRetirementRequest,
    SimulateRetirementResponse,
)

router = APIRouter(prefix="/consumer/pf", tags=["Consumer PF"])

ACHIEVEMENT_SEED = [
    {
        "code": "FIRST_RETIREMENT",
        "name": "Primeira Aposentadoria",
        "description": "Aposentou seu primeiro mHEC",
        "icon": "seed",
        "metric_key": "total_retired_mhec",
        "target_value": 1,
        "points_reward": 100,
        "sort_order": 10,
    },
    {
        "code": "STREAK_7",
        "name": "Streak 7 dias",
        "description": "Compensou consumo por 7 dias seguidos",
        "icon": "flame",
        "metric_key": "current_streak_days",
        "target_value": 7,
        "points_reward": 120,
        "sort_order": 20,
    },
    {
        "code": "STREAK_14",
        "name": "Streak 14 dias",
        "description": "Compensou consumo por 14 dias seguidos",
        "icon": "flame",
        "metric_key": "current_streak_days",
        "target_value": 14,
        "points_reward": 200,
        "sort_order": 30,
    },
    {
        "code": "RETIRE_100",
        "name": "100 mHECs",
        "description": "Aposentou 100 mHECs no total",
        "icon": "tree",
        "metric_key": "total_retired_mhec",
        "target_value": 100,
        "points_reward": 180,
        "sort_order": 40,
    },
    {
        "code": "RETIRE_300",
        "name": "300 mHECs",
        "description": "Atingiu nivel Bosque",
        "icon": "forest",
        "metric_key": "total_retired_mhec",
        "target_value": 300,
        "points_reward": 260,
        "sort_order": 50,
    },
    {
        "code": "RETIRE_500",
        "name": "500 mHECs",
        "description": "Evoluir para Floresta",
        "icon": "forest",
        "metric_key": "total_retired_mhec",
        "target_value": 500,
        "points_reward": 320,
        "sort_order": 60,
    },
    {
        "code": "RETIRE_1000",
        "name": "1000 mHECs",
        "description": "Equivalente a 1 MWh limpo",
        "icon": "bolt",
        "metric_key": "total_retired_mhec",
        "target_value": 1000,
        "points_reward": 500,
        "sort_order": 70,
    },
    {
        "code": "CARBON_ZERO_MONTH",
        "name": "Carbono Zero Mes",
        "description": "Registrou 100% de compensacao no mes",
        "icon": "planet",
        "metric_key": "carbon_zero_months",
        "target_value": 1,
        "points_reward": 300,
        "sort_order": 80,
    },
    {
        "code": "REFER_5_FRIENDS",
        "name": "Indicou 5 amigos",
        "description": "Convidou 5 amigos para o ecossistema",
        "icon": "team",
        "metric_key": "total_referrals",
        "target_value": 5,
        "points_reward": 220,
        "sort_order": 90,
    },
]

DNFT_SEED = [
    {
        "tier_level": 1,
        "tier_name": "Semente",
        "min_mhec_required": 0,
        "icon": "seed",
        "benefits_json": ["Bem-vindo ao ecossistema SOA/SOS"],
    },
    {
        "tier_level": 3,
        "tier_name": "Broto",
        "min_mhec_required": 50,
        "icon": "sprout",
        "benefits_json": ["Selo de progressao inicial"],
    },
    {
        "tier_level": 5,
        "tier_name": "Arbusto",
        "min_mhec_required": 150,
        "icon": "flower",
        "benefits_json": ["Desconto inicial em compensacoes"],
    },
    {
        "tier_level": 7,
        "tier_name": "Bosque",
        "min_mhec_required": 300,
        "icon": "tree",
        "benefits_json": ["12% desconto compensacao", "Badge verificado", "Relatorio mensal"],
    },
    {
        "tier_level": 10,
        "tier_name": "Floresta",
        "min_mhec_required": 500,
        "icon": "forest",
        "benefits_json": ["Relatorio ESG detalhado", "Beneficios premium"],
    },
    {
        "tier_level": 15,
        "tier_name": "Bioma",
        "min_mhec_required": 1500,
        "icon": "planet",
        "benefits_json": ["Acesso prioritario a pools regionais", "Reconhecimento comunidade"],
    },
]


def _normalize_document(document_id: str | None) -> str | None:
    if not document_id:
        return None
    normalized = re.sub(r"\D+", "", document_id)
    return normalized or None


def _avatar_from_name(name: str) -> str:
    return avatar_seed_from_name(name)


def _format_joined(dt: datetime | None) -> str:
    if not dt:
        return datetime.utcnow().strftime("%b/%Y")
    return dt.strftime("%b/%Y")


def _current_month_key(base_dt: datetime | None = None) -> str:
    dt = base_dt or datetime.now(timezone.utc)
    return dt.strftime("%Y-%m")


def _month_keys_last_n(n: int) -> list[str]:
    now = datetime.now(timezone.utc).replace(day=1)
    result: list[str] = []
    for i in range(n - 1, -1, -1):
        dt = now - timedelta(days=30 * i)
        result.append(dt.strftime("%Y-%m"))
    return result


def _compute_streak_from_burn_days(burn_days: set[date]) -> int:
    if not burn_days:
        return 0

    today = datetime.utcnow().date()
    anchor = today if today in burn_days else today - timedelta(days=1)
    if anchor not in burn_days:
        return 0

    streak = 0
    current = anchor
    while current in burn_days:
        streak += 1
        current -= timedelta(days=1)
    return streak


def _sync_profile_from_burn_history(
    db: Session,
    user: User,
    profile: ConsumerProfile,
) -> None:
    burn_rows = (
        db.query(BurnCertificate.burned_at, BurnCertificate.energy_kwh)
        .filter(BurnCertificate.user_id == user.user_id)
        .all()
    )
    if not burn_rows:
        return

    total_mhec = 0
    burn_days: set[date] = set()
    monthly_mhec: dict[str, int] = {}

    for burned_at, energy_kwh in burn_rows:
        amount_mhec = max(0, int(round(float(energy_kwh or 0))))
        total_mhec += amount_mhec
        if not burned_at:
            continue
        month_key = burned_at.strftime("%Y-%m")
        monthly_mhec[month_key] = monthly_mhec.get(month_key, 0) + amount_mhec
        burn_days.add(burned_at.date())

    total_co2 = (Decimal(total_mhec) * Decimal("0.00004")).quantize(Decimal("0.0001"))
    profile.total_retired_mhec = total_mhec
    profile.total_co2_avoided_tons = total_co2
    profile.total_trees_equivalent = int(float(total_co2) * 6.6)
    profile.current_streak_days = _compute_streak_from_burn_days(burn_days)
    profile.updated_at = datetime.utcnow()

    existing_snapshots = (
        db.query(ConsumerDashboardSnapshot)
        .filter(ConsumerDashboardSnapshot.user_id == user.user_id)
        .all()
    )
    by_month = {row.reference_month: row for row in existing_snapshots}
    now = datetime.utcnow()

    for month_key, retired_mhec in monthly_mhec.items():
        snapshot = by_month.get(month_key)
        month_co2 = (Decimal(retired_mhec) * Decimal("0.00004")).quantize(Decimal("0.0001"))
        if not snapshot:
            db.add(
                ConsumerDashboardSnapshot(
                    snapshot_id=uuid4(),
                    user_id=user.user_id,
                    reference_month=month_key,
                    consumed_kwh=Decimal(retired_mhec),
                    retired_mhec=retired_mhec,
                    retirement_pct=Decimal("100.00"),
                    co2_avoided_tons=month_co2,
                    created_at=now,
                )
            )
            continue

        snapshot.retired_mhec = retired_mhec
        consumed_kwh = float(snapshot.consumed_kwh or 0)
        if consumed_kwh <= 0:
            snapshot.consumed_kwh = Decimal(retired_mhec)
            consumed_kwh = float(retired_mhec)

        retirement_pct = 0.0 if consumed_kwh <= 0 else min(100.0, (retired_mhec / consumed_kwh) * 100.0)
        snapshot.retirement_pct = Decimal(str(round(retirement_pct, 2)))
        snapshot.co2_avoided_tons = month_co2


def _ensure_consumer_role(db: Session, user: User) -> None:
    ensure_user_role_bindings(db, user)


def _ensure_catalog_seed(db: Session) -> None:
    if db.query(AchievementCatalog).count() > 0:
        return

    now = datetime.utcnow()
    for row in ACHIEVEMENT_SEED:
        db.add(
            AchievementCatalog(
                achievement_id=uuid4(),
                code=row["code"],
                name=row["name"],
                description=row["description"],
                icon=row["icon"],
                metric_key=row["metric_key"],
                target_value=row["target_value"],
                points_reward=row["points_reward"],
                is_active=True,
                sort_order=row["sort_order"],
                created_at=now,
            )
        )


def _ensure_dnft_seed(db: Session) -> None:
    if db.query(DNFTDefinition).count() > 0:
        return

    now = datetime.utcnow()
    for row in DNFT_SEED:
        db.add(
            DNFTDefinition(
                dnft_id=uuid4(),
                tier_level=row["tier_level"],
                tier_name=row["tier_name"],
                min_mhec_required=row["min_mhec_required"],
                icon=row["icon"],
                benefits_json=row["benefits_json"],
                created_at=now,
            )
        )


def _infer_default_person_type(db: Session, user: User) -> str:
    return infer_default_person_type(db, user)


def _get_or_create_profile(db: Session, user: User) -> ConsumerProfile:
    return ensure_consumer_identity(db, user)


def _dnft_progress_from_total(total_mhec: int, tiers: list[DNFTDefinition]):
    if not tiers:
        return {
            "current_level": 1,
            "current_name": "Semente",
            "next_level": 1,
            "next_name": None,
            "next_target": 0,
            "progress_pct": 100.0,
            "mhecs_to_evolve": 0,
            "benefits": [],
        }

    ordered = sorted(tiers, key=lambda t: (t.min_mhec_required, t.tier_level))
    current_idx = 0
    for idx, tier in enumerate(ordered):
        if total_mhec >= tier.min_mhec_required:
            current_idx = idx
        else:
            break

    current = ordered[current_idx]
    next_tier = ordered[current_idx + 1] if current_idx + 1 < len(ordered) else None

    if not next_tier:
        progress_pct = 100.0
        mhecs_to_evolve = 0
        next_level = current.tier_level
        next_name = None
        next_target = current.min_mhec_required
    else:
        interval = max(1, next_tier.min_mhec_required - current.min_mhec_required)
        raw_progress = (total_mhec - current.min_mhec_required) / interval * 100
        progress_pct = min(100.0, max(0.0, raw_progress))
        mhecs_to_evolve = max(0, next_tier.min_mhec_required - total_mhec)
        next_level = next_tier.tier_level
        next_name = next_tier.tier_name
        next_target = next_tier.min_mhec_required

    return {
        "current_level": int(current.tier_level),
        "current_name": current.tier_name,
        "next_level": int(next_level),
        "next_name": next_name,
        "next_target": int(next_target),
        "progress_pct": round(float(progress_pct), 2),
        "mhecs_to_evolve": int(mhecs_to_evolve),
        "benefits": list(current.benefits_json or []),
    }


def _sync_dnft_state(
    db: Session,
    user: User,
    profile: ConsumerProfile,
    tiers: list[DNFTDefinition],
) -> UserDNFTState:
    state = db.query(UserDNFTState).filter(UserDNFTState.user_id == user.user_id).first()
    progress = _dnft_progress_from_total(profile.total_retired_mhec, tiers)
    now = datetime.utcnow()

    if not state:
        state = UserDNFTState(
            state_id=uuid4(),
            user_id=user.user_id,
            current_tier_level=progress["current_level"],
            current_xp_mhec=profile.total_retired_mhec,
            next_tier_level=progress["next_level"],
            next_tier_target_mhec=progress["next_target"],
            progress_pct=progress["progress_pct"],
            created_at=now,
            updated_at=now,
        )
        db.add(state)
        db.flush()
        return state

    old_level = state.current_tier_level
    state.current_tier_level = progress["current_level"]
    state.current_xp_mhec = profile.total_retired_mhec
    state.next_tier_level = progress["next_level"]
    state.next_tier_target_mhec = progress["next_target"]
    state.progress_pct = progress["progress_pct"]
    state.updated_at = now

    if old_level != state.current_tier_level:
        db.add(
            UserDNFTEvent(
                event_id=uuid4(),
                user_id=user.user_id,
                from_tier_level=old_level,
                to_tier_level=state.current_tier_level,
                event_type="upgrade",
                event_payload={"total_retired_mhec": profile.total_retired_mhec},
                created_at=now,
            )
        )

    return state


def _metric_value(
    profile: ConsumerProfile,
    snapshots: list[ConsumerDashboardSnapshot],
    metric_key: str,
) -> int:
    if metric_key == "total_retired_mhec":
        return int(profile.total_retired_mhec or 0)
    if metric_key == "current_streak_days":
        return int(profile.current_streak_days or 0)
    if metric_key == "total_referrals":
        return int(profile.total_referrals or 0)
    if metric_key == "carbon_zero_months":
        return sum(1 for item in snapshots if float(item.retirement_pct or 0) >= 100.0)
    return 0


def _sync_achievements(
    db: Session,
    user: User,
    profile: ConsumerProfile,
    snapshots: list[ConsumerDashboardSnapshot],
) -> tuple[list[AchievementItem], list[str], int]:
    _ensure_catalog_seed(db)

    catalog = (
        db.query(AchievementCatalog)
        .filter(AchievementCatalog.is_active.is_(True))
        .order_by(AchievementCatalog.sort_order.asc(), AchievementCatalog.created_at.asc())
        .all()
    )
    existing = {
        row.achievement_id: row
        for row in db.query(UserAchievement).filter(UserAchievement.user_id == user.user_id).all()
    }

    now = datetime.utcnow()
    unlocked_codes: list[str] = []
    points_delta = 0
    items: list[AchievementItem] = []

    for ach in catalog:
        progress_value = _metric_value(profile, snapshots, ach.metric_key)
        done = progress_value >= ach.target_value
        progress_pct = 100.0 if ach.target_value <= 0 else min(100.0, (progress_value / ach.target_value) * 100.0)

        row = existing.get(ach.achievement_id)
        if not row:
            row = UserAchievement(
                user_achievement_id=uuid4(),
                user_id=user.user_id,
                achievement_id=ach.achievement_id,
                created_at=now,
            )
            db.add(row)
            existing[ach.achievement_id] = row

        row.progress_value = progress_value
        row.updated_at = now

        if done and not row.is_unlocked:
            row.is_unlocked = True
            row.unlocked_at = now
            unlocked_codes.append(ach.code)

            already_rewarded = (
                db.query(ConsumerRewardLedger)
                .filter(
                    ConsumerRewardLedger.user_id == user.user_id,
                    ConsumerRewardLedger.source_type == "achievement",
                    ConsumerRewardLedger.source_ref == ach.code,
                )
                .first()
            )
            if not already_rewarded:
                points_delta += int(ach.points_reward or 0)
                db.add(
                    ConsumerRewardLedger(
                        ledger_id=uuid4(),
                        user_id=user.user_id,
                        source_type="achievement",
                        source_ref=ach.code,
                        points_delta=int(ach.points_reward or 0),
                        mhec_delta=0,
                        description=f"Achievement unlocked: {ach.name}",
                        created_at=now,
                    )
                )

        items.append(
            AchievementItem(
                code=ach.code,
                icon=ach.icon,
                name=ach.name,
                description=ach.description,
                done=bool(row.is_unlocked),
                progress_pct=round(float(progress_pct), 2),
                progress_value=progress_value,
                target_value=int(ach.target_value),
                unlocked_at=row.unlocked_at,
            )
        )

    if points_delta:
        profile.premmia_points = int(profile.premmia_points or 0) + points_delta

    return items, unlocked_codes, points_delta


def _current_roles(db: Session, user: User) -> list[str]:
    roles = (
        db.query(UserRoleBinding.role_code)
        .filter(UserRoleBinding.user_id == user.user_id)
        .order_by(UserRoleBinding.created_at.asc())
        .all()
    )
    role_values = [r[0] for r in roles]
    if user.role == "seller" and "generator" not in role_values:
        role_values.append("generator")
    if "consumer" not in role_values:
        role_values.append("consumer")
    return sorted(set(role_values))


def _load_monthly_footprint(db: Session, user: User) -> list[ConsumerDashboardSnapshot]:
    return (
        db.query(ConsumerDashboardSnapshot)
        .filter(ConsumerDashboardSnapshot.user_id == user.user_id)
        .order_by(ConsumerDashboardSnapshot.reference_month.desc())
        .limit(6)
        .all()
    )


def _monthly_footprint_response(snapshots: list[ConsumerDashboardSnapshot]) -> list[MonthlyFootprintItem]:
    if not snapshots:
        return [
            MonthlyFootprintItem(month=month, consumed_kwh=0.0, retired_mhec=0, retirement_pct=0.0)
            for month in _month_keys_last_n(6)
        ]

    ordered = sorted(snapshots, key=lambda item: item.reference_month)
    return [
        MonthlyFootprintItem(
            month=item.reference_month,
            consumed_kwh=float(item.consumed_kwh or 0),
            retired_mhec=int(item.retired_mhec or 0),
            retirement_pct=round(float(item.retirement_pct or 0), 2),
        )
        for item in ordered
    ]


def _dnft_history(tiers: list[DNFTDefinition]) -> list[DNFTTierHistoryItem]:
    ordered = sorted(tiers, key=lambda row: (row.min_mhec_required, row.tier_level))
    return [
        DNFTTierHistoryItem(
            level=int(item.tier_level),
            name=item.tier_name,
            at=int(item.min_mhec_required),
            icon=item.icon or "",
        )
        for item in ordered
    ]


def _build_dnft_summary(profile: ConsumerProfile, tiers: list[DNFTDefinition]) -> DNFTSummary:
    progress = _dnft_progress_from_total(profile.total_retired_mhec, tiers)
    return DNFTSummary(
        name=f"Placa Solar - {progress['current_name']}",
        tier=progress["current_level"],
        progress=progress["progress_pct"],
        mhecs_to_evolve=progress["mhecs_to_evolve"],
        benefits=progress["benefits"],
        history=_dnft_history(tiers),
    )


def _build_user_summary(
    db: Session,
    user: User,
    profile: ConsumerProfile,
    tiers: list[DNFTDefinition],
) -> PFUserSummary:
    progress = _dnft_progress_from_total(profile.total_retired_mhec, tiers)
    roles = _current_roles(db, user)
    return PFUserSummary(
        user_id=user.user_id,
        name=profile.display_name or user.name,
        email=user.email,
        avatar=profile.avatar_seed or _avatar_from_name(user.name),
        plan=profile.plan_name or "Ouro Verde",
        person_type=profile.person_type,
        premmia_id=profile.premmia_id,
        premmia_points=int(profile.premmia_points or 0),
        level=progress["current_level"],
        level_name=progress["current_name"],
        next_level=progress["next_name"],
        next_level_at=progress["next_target"],
        current_xp=int(profile.total_retired_mhec or 0),
        pct_level=progress["progress_pct"],
        total_retired_mhec=int(profile.total_retired_mhec or 0),
        co2_avoided_tons=round(float(profile.total_co2_avoided_tons or 0), 4),
        trees_equivalent=int(profile.total_trees_equivalent or 0),
        streak_days=int(profile.current_streak_days or 0),
        joined_date=_format_joined(profile.joined_at),
        roles=roles,
    )


def _build_leaderboard(db: Session, user: User, tiers: list[DNFTDefinition]) -> list[LeaderboardItem]:
    tier_name_by_level = {int(row.tier_level): row.tier_name for row in tiers}

    rows = (
        db.query(ConsumerProfile, User)
        .join(User, User.user_id == ConsumerProfile.user_id)
        .order_by(
            desc(ConsumerProfile.total_retired_mhec),
            desc(ConsumerProfile.current_streak_days),
            ConsumerProfile.joined_at.asc(),
        )
        .limit(10)
        .all()
    )
    data: list[LeaderboardItem] = []
    seen_user = False

    for idx, (profile, owner) in enumerate(rows, start=1):
        level_data = _dnft_progress_from_total(int(profile.total_retired_mhec or 0), tiers)
        is_user = owner.user_id == user.user_id
        seen_user = seen_user or is_user
        data.append(
            LeaderboardItem(
                position=idx,
                name=profile.display_name or owner.name,
                retired_mhec=int(profile.total_retired_mhec or 0),
                level_name=tier_name_by_level.get(level_data["current_level"], level_data["current_name"]),
                streak_days=int(profile.current_streak_days or 0),
                is_user=is_user,
            )
        )

    if seen_user:
        return data

    profile = db.query(ConsumerProfile).filter(ConsumerProfile.user_id == user.user_id).first()
    if not profile:
        return data

    better = (
        db.query(ConsumerProfile)
        .filter(
            or_(
                ConsumerProfile.total_retired_mhec > profile.total_retired_mhec,
                and_(
                    ConsumerProfile.total_retired_mhec == profile.total_retired_mhec,
                    ConsumerProfile.current_streak_days > profile.current_streak_days,
                ),
            )
        )
        .count()
    )
    level_data = _dnft_progress_from_total(int(profile.total_retired_mhec or 0), tiers)
    data.append(
        LeaderboardItem(
            position=better + 1,
            name=profile.display_name or user.name,
            retired_mhec=int(profile.total_retired_mhec or 0),
            level_name=tier_name_by_level.get(level_data["current_level"], level_data["current_name"]),
            streak_days=int(profile.current_streak_days or 0),
            is_user=True,
        )
    )
    return data


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


def _assemble_dashboard(db: Session, user: User) -> ConsumerPFDashboardResponse:
    _ensure_catalog_seed(db)
    _ensure_dnft_seed(db)
    _ensure_consumer_role(db, user)
    profile = _get_or_create_profile(db, user)
    _sync_profile_from_burn_history(db, user, profile)

    tiers = db.query(DNFTDefinition).order_by(DNFTDefinition.min_mhec_required.asc()).all()
    _sync_dnft_state(db, user, profile, tiers)

    snapshots = _load_monthly_footprint(db, user)
    achievements, _, _ = _sync_achievements(db, user, profile, snapshots)

    db.flush()
    return ConsumerPFDashboardResponse(
        user=_build_user_summary(db, user, profile, tiers),
        dnft=_build_dnft_summary(profile, tiers),
        achievements=achievements,
        monthly_footprint=_monthly_footprint_response(snapshots),
        leaderboard=_build_leaderboard(db, user, tiers),
    )


@router.get(
    "/dashboard",
    response_model=ConsumerPFDashboardResponse,
    summary="PF dashboard state (user, dNFT, achievements, footprint and leaderboard)",
)
def get_dashboard(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    payload = _assemble_dashboard(db, user)
    db.commit()
    return payload


@router.get(
    "/dnft",
    response_model=DNFTSummary,
    summary="dNFT summary for authenticated PF user",
)
def get_dnft_summary(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_dnft_seed(db)
    profile = _get_or_create_profile(db, user)
    _sync_profile_from_burn_history(db, user, profile)
    tiers = db.query(DNFTDefinition).order_by(DNFTDefinition.min_mhec_required.asc()).all()
    _sync_dnft_state(db, user, profile, tiers)
    db.commit()
    return _build_dnft_summary(profile, tiers)


@router.get(
    "/achievements",
    response_model=list[AchievementItem],
    summary="Achievement list and progress for authenticated PF user",
)
def get_achievements(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_catalog_seed(db)
    profile = _get_or_create_profile(db, user)
    _sync_profile_from_burn_history(db, user, profile)
    snapshots = _load_monthly_footprint(db, user)
    achievements, _, _ = _sync_achievements(db, user, profile, snapshots)
    db.commit()
    return achievements


@router.put(
    "/profile",
    response_model=PFUserSummary,
    summary="Create or update PF/PJ consumer profile",
)
def upsert_profile(
    req: ConsumerProfileUpsertRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_consumer_role(db, user)
    profile = _get_or_create_profile(db, user)

    normalized_document = _normalize_document(req.document_id)
    if normalized_document:
        duplicated = (
            db.query(ConsumerProfile)
            .filter(
                ConsumerProfile.document_id == normalized_document,
                ConsumerProfile.user_id != user.user_id,
            )
            .first()
        )
        if duplicated:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Documento {normalized_document} ja cadastrado para outro usuario",
            )

    profile.person_type = req.person_type
    profile.document_id = normalized_document
    profile.display_name = req.display_name or profile.display_name or user.name
    profile.avatar_seed = req.avatar_seed or profile.avatar_seed or _avatar_from_name(profile.display_name or user.name)
    profile.plan_name = req.plan_name or profile.plan_name or "Ouro Verde"
    profile.updated_at = datetime.utcnow()

    _ensure_dnft_seed(db)
    tiers = db.query(DNFTDefinition).order_by(DNFTDefinition.min_mhec_required.asc()).all()
    _sync_dnft_state(db, user, profile, tiers)

    db.commit()
    return _build_user_summary(db, user, profile, tiers)


@router.post(
    "/retirements/simulate",
    response_model=SimulateRetirementResponse,
    summary="Simulate PF retirement to unlock dNFT and achievements quickly",
)
def simulate_retirement(
    req: SimulateRetirementRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_catalog_seed(db)
    _ensure_dnft_seed(db)
    _ensure_consumer_role(db, user)
    profile = _get_or_create_profile(db, user)

    now = datetime.utcnow()
    amount = int(req.amount_mhec)

    profile.total_retired_mhec = int(profile.total_retired_mhec or 0) + amount
    profile.current_streak_days = int(profile.current_streak_days or 0) + 1

    co2_delta = Decimal(str(amount)) * Decimal("0.00004")
    profile.total_co2_avoided_tons = Decimal(str(profile.total_co2_avoided_tons or 0)) + co2_delta
    profile.total_trees_equivalent = int(float(profile.total_co2_avoided_tons) * 6.6)
    profile.updated_at = now

    current_month = _current_month_key(now)
    snapshot = (
        db.query(ConsumerDashboardSnapshot)
        .filter(
            ConsumerDashboardSnapshot.user_id == user.user_id,
            ConsumerDashboardSnapshot.reference_month == current_month,
        )
        .first()
    )
    if not snapshot:
        snapshot = ConsumerDashboardSnapshot(
            snapshot_id=uuid4(),
            user_id=user.user_id,
            reference_month=current_month,
            consumed_kwh=Decimal("0"),
            retired_mhec=0,
            retirement_pct=Decimal("0"),
            co2_avoided_tons=Decimal("0"),
            created_at=now,
        )
        db.add(snapshot)

    snapshot.retired_mhec = int(snapshot.retired_mhec or 0) + amount
    if req.consumed_kwh > 0:
        snapshot.consumed_kwh = Decimal(str(req.consumed_kwh))
    consumed_kwh = float(snapshot.consumed_kwh or 0)
    retirement_pct = 0.0 if consumed_kwh <= 0 else min(100.0, (snapshot.retired_mhec / consumed_kwh) * 100.0)
    snapshot.retirement_pct = Decimal(str(round(retirement_pct, 2)))
    snapshot.co2_avoided_tons = Decimal(str(snapshot.co2_avoided_tons or 0)) + co2_delta

    db.add(
        ConsumerRewardLedger(
            ledger_id=uuid4(),
            user_id=user.user_id,
            source_type="burn",
            source_ref=f"simulate:{now.isoformat()}",
            points_delta=amount // 10,
            mhec_delta=amount,
            description=f"Simulated retirement of {amount} mHEC",
            created_at=now,
        )
    )
    profile.premmia_points = int(profile.premmia_points or 0) + (amount // 10)

    tiers = db.query(DNFTDefinition).order_by(DNFTDefinition.min_mhec_required.asc()).all()
    _sync_dnft_state(db, user, profile, tiers)

    snapshots = _load_monthly_footprint(db, user)
    _, unlocked_codes, points_from_unlocks = _sync_achievements(db, user, profile, snapshots)

    db.commit()
    return SimulateRetirementResponse(
        amount_mhec=amount,
        total_retired_mhec=int(profile.total_retired_mhec or 0),
        unlocked_achievements=unlocked_codes,
        points_delta=(amount // 10) + points_from_unlocks,
        premmia_points=int(profile.premmia_points or 0),
        message="Simulacao registrada. dNFT e conquistas recalculados.",
    )
