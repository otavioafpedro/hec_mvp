"""
Testes automatizados — Confidence Score Oficial (SENTINEL AGIS 2.0)

Pesos oficiais:
  C1 Assinatura = 20
  C2 NTP        = 20
  C3 Física     = 30
  C4 Satélite   = 15
  C5 Consenso   = 15
  ─────────────────
  Total         = 100

Thresholds:
  >= 95  → APPROVED
  85–94  → REVIEW
  < 85   → REJECTED

Cenários cobertos:

  SINGLE LAYER FAILURES:
    ✅ Tudo ok                          → 100  → APPROVED
    ❌ Assinatura fail                   →  80  → REJECTED
    ❌ NTP fail                          →  80  → REJECTED
    ❌ Física fail                       →  70  → REJECTED
    ❌ Satélite fail                     →  85  → REVIEW
    ❌ Consenso fail                     →  85  → REVIEW
    ✅ Consenso inconclusivo (None)      → 100  → APPROVED

  MULTI-LAYER FAILURES:
    ❌ NTP + satélite                    →  65  → REJECTED
    ❌ NTP + consenso                    →  65  → REJECTED
    ❌ Satélite + consenso               →  70  → REJECTED
    ❌ Física + satélite                 →  55  → REJECTED
    ❌ Física + NTP                      →  50  → REJECTED
    ❌ Física + satélite + consenso      →  40  → REJECTED
    ❌ Tudo fail                         →   0  → REJECTED

  THRESHOLD BOUNDARIES:
    ✅ Score 95 exato → APPROVED
    ✅ Score 94       → REVIEW
    ✅ Score 85 exato → REVIEW
    ✅ Score 84       → REJECTED

  BREAKDOWN VERIFICATION:
    ✅ Pesos corretos no breakdown
    ✅ Thresholds corretos
    ✅ Inputs refletidos no resultado

  INTEGRATION (POST /telemetry):
    ✅ Resposta inclui confidence_breakdown com pesos
    ✅ Validation record salva breakdown em validation_details
"""
import uuid
from datetime import datetime, timezone, timedelta

import pytest

from app.confidence import (
    calculate_confidence,
    ConfidenceBreakdown,
    status_from_score,
    WEIGHT_SIGNATURE,
    WEIGHT_NTP,
    WEIGHT_PHYSICS,
    WEIGHT_SATELLITE,
    WEIGHT_CONSENSUS,
    TOTAL_WEIGHT,
    THRESHOLD_APPROVED,
    THRESHOLD_REVIEW,
)
from app.security import canonical_payload, sign_payload
from app.api.telemetry import set_server_now_fn, reset_server_now_fn
from app.satellite import MockSatelliteProvider, set_satellite_provider, reset_satellite_provider


SEED_PLANT_ID = "00000000-0000-0000-0000-000000000001"
NOON_UTC = datetime(2026, 2, 23, 14, 30, 0, 0, tzinfo=timezone.utc)
_SAT_PROVIDER = MockSatelliteProvider(fixed_ghi_wm2=800.0, fixed_cloud_cover_pct=10.0)


# ═══════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════

def _fixed_clock(dt):
    return lambda: dt


def _make_payload(private_pem, public_pem, energy_kwh=12.3, nonce=None):
    nonce = nonce or uuid.uuid4().hex[:32]
    ts_str = NOON_UTC.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    canon = canonical_payload(SEED_PLANT_ID, ts_str, 5.5, energy_kwh, nonce)
    signature = sign_payload(private_pem, canon)
    return {
        "plant_id": SEED_PLANT_ID,
        "timestamp": ts_str,
        "power_kw": 5.5,
        "energy_kwh": energy_kwh,
        "signature": signature,
        "public_key": public_pem,
        "nonce": nonce,
    }


# ═══════════════════════════════════════════════════════════════════
# UNIT TESTS — Weights & constants
# ═══════════════════════════════════════════════════════════════════

class TestWeightsAndConstants:
    """Verifica que os pesos oficiais somam 100 e thresholds estão corretos."""

    def test_weights_sum_to_100(self):
        total = WEIGHT_SIGNATURE + WEIGHT_NTP + WEIGHT_PHYSICS + WEIGHT_SATELLITE + WEIGHT_CONSENSUS
        assert total == 100

    def test_individual_weights(self):
        assert WEIGHT_SIGNATURE == 20
        assert WEIGHT_NTP == 20
        assert WEIGHT_PHYSICS == 30
        assert WEIGHT_SATELLITE == 15
        assert WEIGHT_CONSENSUS == 15

    def test_total_weight_constant(self):
        assert TOTAL_WEIGHT == 100

    def test_thresholds(self):
        assert THRESHOLD_APPROVED == 95
        assert THRESHOLD_REVIEW == 85


# ═══════════════════════════════════════════════════════════════════
# UNIT TESTS — Single layer failures
# ═══════════════════════════════════════════════════════════════════

class TestSingleLayerScoring:
    """Falha em uma única camada — verifica score e status."""

    def test_all_pass_100_approved(self):
        """Tudo ok → 100 → APPROVED."""
        r = calculate_confidence(True, True, True, True, True)
        assert r.score == 100.0
        assert r.status == "approved"
        assert r.signature_score == 20
        assert r.ntp_score == 20
        assert r.physics_score == 30
        assert r.satellite_score == 15
        assert r.consensus_score == 15

    def test_signature_fail_80_rejected(self):
        """Assinatura falha → 80 → REJECTED."""
        r = calculate_confidence(False, True, True, True, True)
        assert r.score == 80.0
        assert r.status == "rejected"
        assert r.signature_score == 0
        assert r.ntp_score == 20

    def test_ntp_fail_80_rejected(self):
        """NTP falha → 80 → REJECTED."""
        r = calculate_confidence(True, False, True, True, True)
        assert r.score == 80.0
        assert r.status == "rejected"
        assert r.ntp_score == 0

    def test_physics_fail_70_rejected(self):
        """Física falha → 70 → REJECTED."""
        r = calculate_confidence(True, True, False, True, True)
        assert r.score == 70.0
        assert r.status == "rejected"
        assert r.physics_score == 0

    def test_satellite_fail_85_review(self):
        """Satélite falha → 85 → REVIEW."""
        r = calculate_confidence(True, True, True, False, True)
        assert r.score == 85.0
        assert r.status == "review"
        assert r.satellite_score == 0

    def test_consensus_fail_85_review(self):
        """Consenso divergente → 85 → REVIEW."""
        r = calculate_confidence(True, True, True, True, False)
        assert r.score == 85.0
        assert r.status == "review"
        assert r.consensus_score == 0

    def test_consensus_inconclusive_100_approved(self):
        """Consenso inconclusivo (None) → sem penalidade → 100 → APPROVED."""
        r = calculate_confidence(True, True, True, True, None)
        assert r.score == 100.0
        assert r.status == "approved"
        assert r.consensus_score == 15  # Full weight — no penalty


# ═══════════════════════════════════════════════════════════════════
# UNIT TESTS — Multi-layer failures
# ═══════════════════════════════════════════════════════════════════

class TestMultiLayerScoring:
    """Falhas combinadas — verifica acumulação correta."""

    def test_ntp_plus_satellite_65_rejected(self):
        """NTP + satélite → 65 → REJECTED."""
        r = calculate_confidence(True, False, True, False, True)
        assert r.score == 65.0
        assert r.status == "rejected"

    def test_ntp_plus_consensus_65_rejected(self):
        """NTP + consenso → 65 → REJECTED."""
        r = calculate_confidence(True, False, True, True, False)
        assert r.score == 65.0
        assert r.status == "rejected"

    def test_satellite_plus_consensus_70_rejected(self):
        """Satélite + consenso → 70 → REJECTED."""
        r = calculate_confidence(True, True, True, False, False)
        assert r.score == 70.0
        assert r.status == "rejected"

    def test_physics_plus_satellite_55_rejected(self):
        """Física + satélite → 55 → REJECTED."""
        r = calculate_confidence(True, True, False, False, True)
        assert r.score == 55.0
        assert r.status == "rejected"

    def test_physics_plus_ntp_50_rejected(self):
        """Física + NTP → 50 → REJECTED."""
        r = calculate_confidence(True, False, False, True, True)
        assert r.score == 50.0
        assert r.status == "rejected"

    def test_physics_satellite_consensus_40_rejected(self):
        """Física + satélite + consenso → 40 → REJECTED."""
        r = calculate_confidence(True, True, False, False, False)
        assert r.score == 40.0
        assert r.status == "rejected"

    def test_all_fail_0_rejected(self):
        """Tudo fail → 0 → REJECTED."""
        r = calculate_confidence(False, False, False, False, False)
        assert r.score == 0.0
        assert r.status == "rejected"

    def test_ntp_plus_consensus_inconclusive_80_rejected(self):
        """NTP fail + consenso inconclusivo → 80 → REJECTED."""
        r = calculate_confidence(True, False, True, True, None)
        assert r.score == 80.0
        assert r.status == "rejected"

    def test_satellite_fail_consensus_inconclusive_85_review(self):
        """Satélite fail + consenso inconclusivo → 85 → REVIEW."""
        r = calculate_confidence(True, True, True, False, None)
        assert r.score == 85.0
        assert r.status == "review"


# ═══════════════════════════════════════════════════════════════════
# UNIT TESTS — Threshold boundaries
# ═══════════════════════════════════════════════════════════════════

class TestThresholdBoundaries:
    """Testa os limites exatos dos thresholds."""

    def test_score_100_approved(self):
        assert status_from_score(100.0) == "approved"

    def test_score_95_approved(self):
        assert status_from_score(95.0) == "approved"

    def test_score_94_review(self):
        assert status_from_score(94.0) == "review"

    def test_score_85_review(self):
        assert status_from_score(85.0) == "review"

    def test_score_84_rejected(self):
        assert status_from_score(84.0) == "rejected"

    def test_score_0_rejected(self):
        assert status_from_score(0.0) == "rejected"

    def test_score_95_01_approved(self):
        assert status_from_score(95.01) == "approved"

    def test_score_84_99_rejected(self):
        assert status_from_score(84.99) == "rejected"


# ═══════════════════════════════════════════════════════════════════
# UNIT TESTS — Breakdown completeness
# ═══════════════════════════════════════════════════════════════════

class TestBreakdownCompleteness:
    """Verifica que o breakdown contém todos os campos esperados."""

    def test_result_type(self):
        r = calculate_confidence(True, True, True, True, True)
        assert isinstance(r, ConfidenceBreakdown)

    def test_all_fields_present(self):
        r = calculate_confidence(True, False, True, False, None)
        assert hasattr(r, "score")
        assert hasattr(r, "status")
        assert hasattr(r, "signature_score")
        assert hasattr(r, "ntp_score")
        assert hasattr(r, "physics_score")
        assert hasattr(r, "satellite_score")
        assert hasattr(r, "consensus_score")
        assert hasattr(r, "weights")
        assert hasattr(r, "thresholds")

    def test_weights_dict(self):
        r = calculate_confidence(True, True, True, True, True)
        assert r.weights["signature"] == 20
        assert r.weights["ntp"] == 20
        assert r.weights["physics"] == 30
        assert r.weights["satellite"] == 15
        assert r.weights["consensus"] == 15
        assert r.weights["total"] == 100

    def test_thresholds_dict(self):
        r = calculate_confidence(True, True, True, True, True)
        assert r.thresholds["approved"] == 95
        assert r.thresholds["review"] == 85

    def test_inputs_reflected_in_result(self):
        r = calculate_confidence(True, False, True, False, None)
        assert r.signature_valid is True
        assert r.ntp_pass is False
        assert r.physics_pass is True
        assert r.satellite_pass is False
        assert r.consensus_pass is None

    def test_score_sum_equals_layer_scores(self):
        """Score deve ser a soma exata das pontuações por camada."""
        r = calculate_confidence(True, False, True, False, True)
        expected = r.signature_score + r.ntp_score + r.physics_score + r.satellite_score + r.consensus_score
        assert r.score == expected

    def test_score_clamped_0_100(self):
        """Score nunca excede 0-100."""
        r_max = calculate_confidence(True, True, True, True, True)
        r_min = calculate_confidence(False, False, False, False, False)
        assert 0 <= r_max.score <= 100
        assert 0 <= r_min.score <= 100


# ═══════════════════════════════════════════════════════════════════
# UNIT TESTS — Exhaustive truth table (all 32 combinations of 5 bools)
# ═══════════════════════════════════════════════════════════════════

class TestExhaustiveTruthTable:
    """Verifica que TODAS as combinações de 5 camadas produzem score correto."""

    @pytest.mark.parametrize("sig,ntp,phys,sat,cons", [
        (True,  True,  True,  True,  True),    # 100
        (True,  True,  True,  True,  False),   # 85
        (True,  True,  True,  False, True),    # 85
        (True,  True,  True,  False, False),   # 70
        (True,  True,  False, True,  True),    # 70
        (True,  True,  False, True,  False),   # 55
        (True,  True,  False, False, True),    # 55
        (True,  True,  False, False, False),   # 40
        (True,  False, True,  True,  True),    # 80
        (True,  False, True,  True,  False),   # 65
        (True,  False, True,  False, True),    # 65
        (True,  False, True,  False, False),   # 50
        (True,  False, False, True,  True),    # 50
        (True,  False, False, True,  False),   # 35
        (True,  False, False, False, True),    # 35
        (True,  False, False, False, False),   # 20
        (False, True,  True,  True,  True),    # 80
        (False, True,  True,  True,  False),   # 65
        (False, True,  True,  False, True),    # 65
        (False, True,  True,  False, False),   # 50
        (False, True,  False, True,  True),    # 50
        (False, True,  False, True,  False),   # 35
        (False, True,  False, False, True),    # 35
        (False, True,  False, False, False),   # 20
        (False, False, True,  True,  True),    # 60
        (False, False, True,  True,  False),   # 45
        (False, False, True,  False, True),    # 45
        (False, False, True,  False, False),   # 30
        (False, False, False, True,  True),    # 30
        (False, False, False, True,  False),   # 15
        (False, False, False, False, True),    # 15
        (False, False, False, False, False),   # 0
    ])
    def test_truth_table_score(self, sig, ntp, phys, sat, cons):
        """Cada combinação produz score = soma dos pesos das camadas que passaram."""
        r = calculate_confidence(sig, ntp, phys, sat, cons)
        expected = (
            (20 if sig else 0)
            + (20 if ntp else 0)
            + (30 if phys else 0)
            + (15 if sat else 0)
            + (15 if cons else 0)
        )
        assert r.score == expected
        assert r.status == status_from_score(expected)


# ═══════════════════════════════════════════════════════════════════
# INTEGRATION TESTS — POST /telemetry verifica breakdown no response
# ═══════════════════════════════════════════════════════════════════

class TestConfidenceIntegration:
    """Integração: endpoint retorna breakdown e salva em validation."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        set_server_now_fn(_fixed_clock(NOON_UTC))
        set_satellite_provider(_SAT_PROVIDER)
        yield
        reset_server_now_fn()
        reset_satellite_provider()

    def test_approved_has_full_breakdown(self, client, ecdsa_keys):
        """Score 100 → APPROVED, breakdown presente no response."""
        private_pem, public_pem = ecdsa_keys
        payload = _make_payload(private_pem, public_pem, energy_kwh=12.3)

        r = client.post("/telemetry", json=payload)
        assert r.status_code == 201
        data = r.json()

        assert data["status"] == "accepted"
        assert data["confidence_score"] == 100.0
        bd = data["confidence_breakdown"]
        assert bd is not None
        assert bd["C1_signature"] == 20
        assert bd["C2_ntp"] == 20
        assert bd["C3_physics"] == 30
        assert bd["C4_satellite"] == 15
        assert bd["C5_consensus"] == 15
        assert bd["weights"]["total"] == 100

    def test_breakdown_saved_in_validation_details(self, client, ecdsa_keys, db_session):
        """Validation record salva breakdown em validation_details."""
        private_pem, public_pem = ecdsa_keys
        payload = _make_payload(private_pem, public_pem, energy_kwh=12.3)

        r = client.post("/telemetry", json=payload)
        assert r.status_code == 201
        data = r.json()

        from app.models.models import Validation as Val
        val = db_session.query(Val).filter(
            Val.validation_id == data["validation_id"]
        ).first()

        assert val is not None
        assert val.validation_details is not None
        bd = val.validation_details["confidence_breakdown"]
        assert bd["signature"] == 20
        assert bd["ntp"] == 20
        assert bd["physics"] == 30
        assert bd["satellite"] == 15
        assert bd["consensus"] == 15
        assert val.validation_details["weights"]["total"] == 100
        assert val.validation_details["thresholds"]["approved"] == 95
        assert val.validation_details["thresholds"]["review"] == 85

    def test_rejected_message_format(self, client, ecdsa_keys):
        """Score < 85 → message contém REJECTED e SENTINEL AGIS."""
        set_satellite_provider(
            MockSatelliteProvider(fixed_ghi_wm2=100.0, fixed_cloud_cover_pct=85.0)
        )
        # NTP drift causes fail
        ntp_shifted = NOON_UTC + timedelta(milliseconds=20)
        set_server_now_fn(_fixed_clock(ntp_shifted))

        private_pem, public_pem = ecdsa_keys
        payload = _make_payload(private_pem, public_pem, energy_kwh=30.0)

        r = client.post("/telemetry", json=payload)
        assert r.status_code == 201
        data = r.json()

        # NTP fail + satellite fail → 65 → REJECTED
        assert data["status"] == "rejected"
        assert data["confidence_score"] == 65.0
        assert "REJECTED" in data["message"]
        assert "SENTINEL AGIS" in data["message"]
        # Breakdown shows zeros for failed layers
        bd = data["confidence_breakdown"]
        assert bd["C2_ntp"] == 0
        assert bd["C4_satellite"] == 0
