"""Schemas for PF consumer profile, dNFT and achievements."""
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class ConsumerProfileUpsertRequest(BaseModel):
    person_type: str = Field(..., description="PF or PJ")
    document_id: str = Field(..., min_length=11, max_length=32)
    display_name: str | None = Field(default=None, max_length=255)
    avatar_seed: str | None = Field(default=None, max_length=20)
    plan_name: str | None = Field(default=None, max_length=60)


class PFUserSummary(BaseModel):
    user_id: UUID
    name: str
    email: str
    avatar: str
    plan: str
    person_type: str
    premmia_id: str | None = None
    premmia_points: int
    level: int
    level_name: str
    next_level: str | None = None
    next_level_at: int
    current_xp: int
    pct_level: float
    total_retired_mhec: int
    co2_avoided_tons: float
    trees_equivalent: int
    streak_days: int
    joined_date: str
    roles: list[str] = Field(default_factory=list)


class DNFTTierHistoryItem(BaseModel):
    level: int
    name: str
    at: int
    icon: str = ""


class DNFTSummary(BaseModel):
    name: str
    tier: int
    progress: float
    mhecs_to_evolve: int
    benefits: list[str] = Field(default_factory=list)
    history: list[DNFTTierHistoryItem] = Field(default_factory=list)


class AchievementItem(BaseModel):
    code: str
    icon: str
    name: str
    description: str
    done: bool
    progress_pct: float = 0.0
    progress_value: int = 0
    target_value: int = 0
    unlocked_at: datetime | None = None


class MonthlyFootprintItem(BaseModel):
    month: str
    consumed_kwh: float
    retired_mhec: int
    retirement_pct: float


class LeaderboardItem(BaseModel):
    position: int
    name: str
    retired_mhec: int
    level_name: str
    streak_days: int
    is_user: bool = False


class ConsumerPFDashboardResponse(BaseModel):
    user: PFUserSummary
    dnft: DNFTSummary
    achievements: list[AchievementItem]
    monthly_footprint: list[MonthlyFootprintItem]
    leaderboard: list[LeaderboardItem]


class SimulateRetirementRequest(BaseModel):
    amount_mhec: int = Field(..., ge=1, le=1_000_000)
    consumed_kwh: float = Field(0, ge=0)


class SimulateRetirementResponse(BaseModel):
    amount_mhec: int
    total_retired_mhec: int
    unlocked_achievements: list[str] = Field(default_factory=list)
    points_delta: int = 0
    premmia_points: int = 0
    message: str
