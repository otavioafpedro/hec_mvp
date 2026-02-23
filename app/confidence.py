"""
Confidence Score Oficial — SENTINEL AGIS 2.0

Calcula o score de confiança consolidado (0-100) de cada telemetria
usando pesos oficiais por camada da Fortaleza Lógica.

Pesos (total = 100):
  ┌──────────────────────────┬──────┬───────────────────────────────────┐
  │ Camada                   │ Peso │ Critério                          │
  ├──────────────────────────┼──────┼───────────────────────────────────┤
  │ C1 — Assinatura ECDSA   │  20  │ Válida = 20, Inválida = 0        │
  │ C2 — NTP Blindada ±5ms  │  20  │ Pass = 20, Fail = 0              │
  │ C3 — Física Teórica     │  30  │ Pass = 30, Fail = 0              │
  │ C4 — Satélite           │  15  │ Pass = 15, Fail = 0              │
  │ C5 — Consenso Granular  │  15  │ Pass = 15, Fail = 0, N/A = 15   │
  └──────────────────────────┴──────┴───────────────────────────────────┘

Nota: C5 inconclusivo (None) recebe peso integral (sem penalidade).

Thresholds:
  >= 95  →  APPROVED   (todos os checks passaram)
  85–94  →  REVIEW     (falha leve: NTP ou consenso ou satélite)
  < 85   →  REJECTED   (falha severa: física ou múltiplas camadas)

Exemplos:
  Tudo ok               → 100 → APPROVED
  NTP fail              → 80  → REJECTED
  Satélite fail         → 85  → REVIEW
  Consenso fail         → 85  → REVIEW
  Física fail           → 70  → REJECTED
  NTP + satélite fail   → 65  → REJECTED
  Tudo fail             → 0   → REJECTED
"""
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Pesos oficiais por camada (total = 100)
# ---------------------------------------------------------------------------
WEIGHT_SIGNATURE = 20    # C1 — Assinatura ECDSA + anti-replay
WEIGHT_NTP = 20          # C2 — NTP Blindada ±5ms
WEIGHT_PHYSICS = 30      # C3 — Física Teórica (pvlib)
WEIGHT_SATELLITE = 15    # C4 — Validação Satélite
WEIGHT_CONSENSUS = 15    # C5 — Consenso Granular Geoespacial

TOTAL_WEIGHT = (
    WEIGHT_SIGNATURE + WEIGHT_NTP + WEIGHT_PHYSICS
    + WEIGHT_SATELLITE + WEIGHT_CONSENSUS
)
assert TOTAL_WEIGHT == 100, f"Pesos devem somar 100, somaram {TOTAL_WEIGHT}"


# ---------------------------------------------------------------------------
# Thresholds oficiais
# ---------------------------------------------------------------------------
THRESHOLD_APPROVED = 95   # >= 95 → APPROVED
THRESHOLD_REVIEW = 85     # >= 85 → REVIEW (entre 85 e 94)
                           # < 85  → REJECTED


# ---------------------------------------------------------------------------
# Resultado detalhado do cálculo
# ---------------------------------------------------------------------------

@dataclass
class ConfidenceBreakdown:
    """Detalhamento do score de confiança por camada."""

    # Score final
    score: float                    # 0-100
    status: str                     # approved | review | rejected

    # Pontuação por camada
    signature_score: float          # 0 ou 20
    ntp_score: float                # 0 ou 20
    physics_score: float            # 0 ou 30
    satellite_score: float          # 0 ou 15
    consensus_score: float          # 0 ou 15

    # Inputs usados
    signature_valid: bool
    ntp_pass: bool
    physics_pass: bool
    satellite_pass: bool
    consensus_pass: Optional[bool]  # None = inconclusivo

    # Metadados
    weights: dict = field(default_factory=dict)
    thresholds: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Função principal
# ---------------------------------------------------------------------------

def calculate_confidence(
    signature_valid: bool,
    ntp_pass: bool,
    physics_pass: bool,
    satellite_pass: bool,
    consensus_pass: Optional[bool],  # None = inconclusivo → sem penalidade
) -> ConfidenceBreakdown:
    """
    Calcula o Confidence Score Oficial SENTINEL AGIS 2.0.

    Cada camada da Fortaleza Lógica contribui com peso fixo ao score.
    Se a camada falhar, seu peso é zerado.
    Consensus inconclusivo (None) recebe pontuação integral.

    Args:
        signature_valid: True se ECDSA válida (C1)
        ntp_pass: True se |drift| <= 5ms (C2)
        physics_pass: True se energy <= theoretical_max (C3)
        satellite_pass: True se energy <= satellite_max (C4)
        consensus_pass: True=ok, False=divergente, None=inconclusivo (C5)

    Returns:
        ConfidenceBreakdown com score, status e detalhamento por camada
    """
    # ── Calcular pontuação por camada ────────────────────────────────
    sig_score = WEIGHT_SIGNATURE if signature_valid else 0.0
    ntp_score = WEIGHT_NTP if ntp_pass else 0.0
    phys_score = WEIGHT_PHYSICS if physics_pass else 0.0
    sat_score = WEIGHT_SATELLITE if satellite_pass else 0.0

    # Consenso: None (inconclusivo) = sem penalidade → peso integral
    if consensus_pass is None:
        cons_score = WEIGHT_CONSENSUS  # Inconclusivo → sem penalidade
    elif consensus_pass:
        cons_score = WEIGHT_CONSENSUS  # Aprovado → peso integral
    else:
        cons_score = 0.0               # Divergente → zero

    # ── Score consolidado ────────────────────────────────────────────
    score = sig_score + ntp_score + phys_score + sat_score + cons_score
    score = round(max(0.0, min(100.0, score)), 2)

    # ── Status baseado nos thresholds oficiais ───────────────────────
    if score >= THRESHOLD_APPROVED:
        status = "approved"
    elif score >= THRESHOLD_REVIEW:
        status = "review"
    else:
        status = "rejected"

    return ConfidenceBreakdown(
        score=score,
        status=status,
        signature_score=sig_score,
        ntp_score=ntp_score,
        physics_score=phys_score,
        satellite_score=sat_score,
        consensus_score=cons_score,
        signature_valid=signature_valid,
        ntp_pass=ntp_pass,
        physics_pass=physics_pass,
        satellite_pass=satellite_pass,
        consensus_pass=consensus_pass,
        weights={
            "signature": WEIGHT_SIGNATURE,
            "ntp": WEIGHT_NTP,
            "physics": WEIGHT_PHYSICS,
            "satellite": WEIGHT_SATELLITE,
            "consensus": WEIGHT_CONSENSUS,
            "total": TOTAL_WEIGHT,
        },
        thresholds={
            "approved": THRESHOLD_APPROVED,
            "review": THRESHOLD_REVIEW,
        },
    )


def status_from_score(score: float) -> str:
    """Helper: retorna status string a partir do score numérico."""
    if score >= THRESHOLD_APPROVED:
        return "approved"
    elif score >= THRESHOLD_REVIEW:
        return "review"
    return "rejected"
