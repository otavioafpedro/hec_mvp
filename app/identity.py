from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy.orm import Session

from app.models.models import ConsumerProfile, GeneratorProfile, User, UserRoleBinding

DEFAULT_CONSUMER_PLAN = "Ouro Verde"
PJ_ROLE_HINTS = ("pj", "institutional", "corporate", "company")
MANAGED_ROLE_CODES = {"consumer", "generator", "admin"}


def avatar_seed_from_name(name: str | None) -> str:
    parts = [part for part in (name or "").strip().split() if part]
    if not parts:
        return "SO"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def infer_default_person_type(db: Session, user: User) -> str:
    generator_profile = (
        db.query(GeneratorProfile.person_type)
        .filter(GeneratorProfile.user_id == user.user_id)
        .first()
    )
    if generator_profile and generator_profile[0] in {"PF", "PJ"}:
        return generator_profile[0]

    role_values = {
        str(value or "").strip().lower()
        for (value,) in (
            db.query(UserRoleBinding.role_code)
            .filter(UserRoleBinding.user_id == user.user_id)
            .all()
        )
    }
    role_values.add(str(user.role or "").strip().lower())
    role_haystack = " ".join(sorted(role_values))

    if any(hint in role_haystack for hint in PJ_ROLE_HINTS):
        return "PJ"

    return "PF"


def ensure_user_role_bindings(db: Session, user: User) -> dict[str, UserRoleBinding]:
    normalized_user_role = str(user.role or "").strip().lower()
    has_generator_profile = (
        db.query(GeneratorProfile.user_id)
        .filter(GeneratorProfile.user_id == user.user_id)
        .first()
        is not None
    )

    desired_roles = {"consumer"}
    if normalized_user_role == "admin":
        desired_roles.add("admin")
    if normalized_user_role == "seller" or has_generator_profile:
        desired_roles.add("generator")

    primary_role = "consumer"
    if normalized_user_role == "admin":
        primary_role = "admin"
    elif normalized_user_role == "seller":
        primary_role = "generator"

    existing = {
        binding.role_code: binding
        for binding in (
            db.query(UserRoleBinding)
            .filter(UserRoleBinding.user_id == user.user_id)
            .all()
        )
    }
    now = datetime.utcnow()

    for role_code in sorted(desired_roles):
        desired_primary = role_code == primary_role
        binding = existing.get(role_code)
        if binding is None:
            binding = UserRoleBinding(
                binding_id=uuid4(),
                user_id=user.user_id,
                role_code=role_code,
                is_primary=desired_primary,
                created_at=now,
            )
            db.add(binding)
            existing[role_code] = binding
            continue

        if binding.is_primary != desired_primary:
            binding.is_primary = desired_primary

    for role_code, binding in existing.items():
        if role_code in MANAGED_ROLE_CODES and role_code not in desired_roles and binding.is_primary:
            binding.is_primary = False

    return existing


def ensure_consumer_identity(db: Session, user: User) -> ConsumerProfile:
    ensure_user_role_bindings(db, user)

    now = datetime.utcnow()
    desired_person_type = infer_default_person_type(db, user)
    profile = (
        db.query(ConsumerProfile)
        .filter(ConsumerProfile.user_id == user.user_id)
        .first()
    )

    if not profile:
        profile = ConsumerProfile(
            profile_id=uuid4(),
            user_id=user.user_id,
            person_type=desired_person_type,
            display_name=user.name,
            avatar_seed=avatar_seed_from_name(user.name),
            plan_name=DEFAULT_CONSUMER_PLAN,
            premmia_id=f"PRM-{str(user.user_id).replace('-', '')[:7].upper()}",
            premmia_points=0,
            current_streak_days=0,
            total_retired_mhec=0,
            total_co2_avoided_tons=Decimal("0"),
            total_trees_equivalent=0,
            total_referrals=0,
            joined_at=user.created_at or now,
            created_at=now,
            updated_at=now,
        )
        db.add(profile)
        db.flush()
        return profile

    updated = False
    generator_person_type = (
        db.query(GeneratorProfile.person_type)
        .filter(GeneratorProfile.user_id == user.user_id)
        .first()
    )
    if (
        desired_person_type in {"PF", "PJ"}
        and profile.person_type != desired_person_type
        and (
            profile.person_type not in {"PF", "PJ"}
            or (generator_person_type and generator_person_type[0] in {"PF", "PJ"})
        )
    ):
        profile.person_type = desired_person_type
        updated = True

    if not (profile.display_name or "").strip() and (user.name or "").strip():
        profile.display_name = user.name
        updated = True

    if not (profile.avatar_seed or "").strip():
        profile.avatar_seed = avatar_seed_from_name(profile.display_name or user.name)
        updated = True

    if not (profile.plan_name or "").strip():
        profile.plan_name = DEFAULT_CONSUMER_PLAN
        updated = True

    if not (profile.premmia_id or "").strip():
        profile.premmia_id = f"PRM-{str(user.user_id).replace('-', '')[:7].upper()}"
        updated = True

    if not profile.joined_at:
        profile.joined_at = user.created_at or now
        updated = True

    if updated:
        profile.updated_at = now
        db.flush()

    return profile
