# Validation Engine MVP - End-to-End Flow

## Purpose
This document explains how the MVP works from start to finish, including validation, certificate issuance, marketplace flow, and burn flow.

## Scope
This flow covers:
- telemetry ingestion with ECDSA signature and nonce
- 5-layer validation and confidence scoring
- HEC issuance (JSON + PDF + IPFS + on-chain mock registry)
- lot creation
- marketplace registration and buy
- burn certificate generation

## Runtime Architecture
- `db` service: PostgreSQL + TimescaleDB + PostGIS
- `api` service: FastAPI application
- migrations: Alembic runs on startup
- seed: one plant is inserted (`00000000-0000-0000-0000-000000000001`)
- providers used by default in MVP:
  - satellite: mock
  - IPFS: mock
  - blockchain: mock

## Startup Sequence
1. `docker compose up --build -d`
2. API waits for DB readiness.
3. Alembic runs all migrations.
4. Seed script inserts default plant and prints test keypair.
5. API starts on port `8000`.
6. Health check: `GET /health`

## Business Flow
1. Telemetry ingest (`POST /telemetry`)
- Input includes `plant_id`, `timestamp`, `power_kw`, `energy_kwh`, `signature`, `public_key`, `nonce`.
- Security checks:
  - ECDSA signature verification
  - nonce anti-replay
  - SHA-256 payload hash
- Validation checks:
  - C2 NTP drift
  - C3 physics bound
  - C4 satellite cross-check
  - C5 neighbor consensus
- Output:
  - validation record
  - confidence score
  - status (`accepted`, `review`, or `rejected`)
  - optional auto-issued `hec_id` when approved

2. HEC issue (`POST /hec/issue`) if needed
- Requires validation status `approved`.
- Generates certificate JSON and hash.
- Generates certificate PDF.
- Uploads JSON/PDF to mock IPFS.
- Registers hash + CID on mock blockchain.
- Saves HEC as `registered`.

3. HEC integrity checks
- `GET /hec/verify/{hec_id}` verifies IPFS JSON hash match.
- `GET /hec/onchain/{hec_id}` confirms on-chain registry entry.

4. Lot creation (`POST /lots/create`)
- Accepts one or more backed HEC IDs.
- Enforces full backing (`ipfs_json_cid` + `registry_tx_hash`).
- Creates lot with quantity and energy totals.

5. Marketplace buyer flow
- Register (`POST /marketplace/register`) creates user + wallet + token.
- Buy (`POST /marketplace/buy`) consumes lot quantity and credits wallet HEC balance.

6. Burn flow (`POST /burn`)
- Burns owned HECs irreversibly.
- Generates burn certificate JSON + PDF.
- Uploads to IPFS mock.
- Registers burn certificate on-chain mock.

## Important MVP Notes
- NTP gate is strict (`+/- 5ms`). In local environments, telemetry often becomes `review`/`rejected` only because of drift.
- For demonstration, you can promote one validation row to `approved` in DB and then call `/hec/issue`.
- Providers are mock by default, so no external blockchain/IPFS setup is needed for MVP proof.

## Permanent Fixes Already Applied
- `telemetry.pre_commitment_hash` expanded to `VARCHAR(256)` via migration `012_precommitment_hash_len`.
- `reportlab` added to dependencies to support PDF generation for HEC and burn certificates.

## MVP Acceptance Checklist
- `GET /health` is healthy.
- Telemetry request returns `201`.
- HEC can be issued and returns `status=registered`.
- IPFS verify returns `verified=true`.
- On-chain verify returns `exists=true`.
- Lot can be created.
- Buyer can register and buy.
- Burn succeeds and returns `burn_id`.
