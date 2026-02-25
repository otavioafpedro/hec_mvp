# Backend MVP Deploy TODO (Engine Only)

## Goal
Put the backend engine in deployable state to:
- receive inverter telemetry reliably
- validate generation with DS logic (physics/satellite/consensus)
- issue HEC with digital backing
- support marketplace buy and burn flows
- run without frontend dependency

## Current State Snapshot
- API + DB stack runs with Docker (`/health` OK).
- Full backend flow works in MVP mode (`telemetry -> issue HEC -> verify -> lot -> buy -> burn`).
- HEC flow currently means **registry-backed certificate** (hash + IPFS + on-chain registry).
- Critical integrations still mock/stub by default:
  - blockchain real provider not implemented (`app/blockchain.py`)
  - IPFS Pinata/local providers not implemented (`app/ipfs_service.py`)
  - INPE/CAMS providers fallback to mock (`app/satellite.py`)

## P0 - Must Have Before Pilot Deploy

## 1) Runtime and deploy hardening
- [ ] Remove dev reload mode in production (`entrypoint.sh` currently uses `uvicorn --reload`).
- [ ] Split compose files: `docker-compose.dev.yml` (bind mounts) vs `docker-compose.prod.yml` (no source mount).
- [ ] Add `.env.example` and `.env.production` template with required vars.
- [ ] Pin image tags for DB/OS dependencies to avoid unexpected runtime changes.

## 2) Security baseline
- [ ] Move `TOKEN_SECRET` from hardcoded value to env var (`app/auth.py`).
- [ ] Replace SHA-256 password hashing with `bcrypt`/`argon2`.
- [ ] Move default DB credentials out of code/compose (`app/config.py`, `docker-compose.yml`).
- [ ] Add auth/rate-limit protections for telemetry endpoint (gateway or app-level).

## 3) Inverter ingestion contract
- [ ] Define stable telemetry payload version (`schema_version`, `device_id`, `fw_version`).
- [ ] Implement device registry + key provisioning process (public key lifecycle, rotation, revoke).
- [ ] Define replay/clock policy for devices in field (retry windows, timeout behavior).
- [ ] Add idempotency key strategy for duplicated inverter sends.

## 4) NTP policy for real world devices
- [ ] Revisit strict `NTP_MAX_DRIFT_MS=5.0` for field usage (`app/security.py`).
- [ ] Decide policy:
  - keep strict gate only for high-assurance channels, OR
  - relax drift window and score in confidence instead of hard reject.
- [ ] Store both `device_timestamp` and `server_received_at` to support forensic checks.

## 5) Data Science validation readiness
- [ ] Decide MVP mode:
  - Mode A: keep mock providers (demo only), or
  - Mode B: integrate real INPE/CAMS data for pilot.
- [ ] Implement real provider clients and failure fallbacks with explicit status flags.
- [ ] Define calibration process for thresholds by plant profile (capacity, geography, season).
- [ ] Add validation KPI dashboard inputs (approval/review/reject rates and causes).

## 6) HEC issuance semantics (important business decision)
- [ ] Confirm if "minted" means:
  - A) register certificate hash on-chain only (current model), or
  - B) mint NFT token (ERC-721/1155) per certificate.
- [ ] If B: implement mint contract + tx flow + token_id persistence + transfer/burn semantics.
- [ ] Align status model (`pending/registered/minted/listed/sold/retired`) with real chain lifecycle.

## 7) IPFS and blockchain production integration
- [ ] Implement `PinataProvider` upload/download/pin in `app/ipfs_service.py`.
- [ ] Implement `PolygonProvider.register/verify` in `app/blockchain.py`.
- [ ] Secure key management (vault/KMS, no plain private key in repo).
- [ ] Add tx retry policy + timeout + reconciliation worker.

## 8) Test strategy for deploy confidence
- [ ] Replace SQLite-based failing tests for JSONB models with Postgres-backed integration tests.
- [ ] Add CI pipeline:
  - build image
  - run migrations
  - run backend smoke suite
  - publish artifact
- [ ] Keep one deterministic smoke script as release gate.

## 9) Observability and operations
- [ ] Structured logs (JSON) with request ids.
- [ ] Metrics for ingestion and validation pipeline (latency, rejects, replay hits, HEC issue failures).
- [ ] Alerts for critical failures (DB, migration, HEC issue, burn failures).
- [ ] Backup and restore runbook for Postgres/Timescale.

## 10) Documentation and handover
- [ ] Keep docs updated:
  - `docs/END_TO_END_FLOW.md`
  - `docs/STEP_BY_STEP_SAMPLES.md`
- [ ] Add operational runbook for on-call:
  - startup
  - migrations
  - rollback
  - smoke verification

## P1 - Should Have Right After Pilot Start
- [ ] Async queue for heavy steps (PDF, IPFS, chain tx) to reduce API latency spikes.
- [ ] Dead-letter and replay process for failed issuance/burn jobs.
- [ ] Device-level analytics (per inverter reliability, drift trends, anomaly recurrence).
- [ ] Marketplace anti-fraud checks and transaction audit exports.

## P2 - Next Evolution
- [ ] Real external event ingestion channel (MQTT/Kafka) with authenticated producer flow.
- [ ] Multi-tenant support and role separation.
- [ ] Dedicated chain indexer/reconciler service.
- [ ] Frontend integration contracts (once backend SLA is stable).

## Definition of Done (Backend MVP Deploy)
- [ ] API survives restart with zero manual hotfix steps.
- [ ] Telemetry endpoint handles real inverter traffic format and signature policy.
- [ ] Validation pipeline produces deterministic decisions with auditable reasons.
- [ ] HEC issuance and verification run successfully in target environment.
- [ ] Lot, buy, and burn flows complete with auditable records.
- [ ] CI/CD and smoke tests gate releases.
- [ ] Secrets are externalized and rotated.
