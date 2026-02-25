# Validation Engine MVP - Step-by-Step Samples

## 0) Start Services
```powershell
Set-Location D:\tech.castling\hec_mvp\hec_mvp
docker compose up --build -d
Invoke-RestMethod http://localhost:8000/health
```

Expected: `status: healthy`.

---

## 1) Telemetry Ingestion Sample (`POST /telemetry`)

### 1.1 Generate signed payload and send request
```powershell
@'
import uuid
from datetime import datetime, timezone
import httpx
from app.security import generate_ecdsa_keypair, canonical_payload, sign_payload

BASE = "http://localhost:8000"
PLANT_ID = "00000000-0000-0000-0000-000000000001"

private_pem, public_pem = generate_ecdsa_keypair()
ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
nonce = uuid.uuid4().hex[:32]
canon = canonical_payload(PLANT_ID, ts, 5.5, 12.3, nonce)
sig = sign_payload(private_pem, canon)

payload = {
    "plant_id": PLANT_ID,
    "timestamp": ts,
    "power_kw": 5.5,
    "energy_kwh": 12.3,
    "signature": sig,
    "public_key": public_pem,
    "nonce": nonce
}

r = httpx.post(f"{BASE}/telemetry", json=payload, timeout=20)
print("STATUS", r.status_code)
print(r.text)
'@ | python -
```

### 1.2 Typical response
- Status `201`
- Body includes:
  - `validation_id`
  - `confidence_score`
  - `status` (`accepted`, `review`, or `rejected`)
  - `hec_id` (only when approved/auto-issued)

---

## 2) If `hec_id` is null, promote validation for MVP demo
Local demo can fail strict NTP (`+/- 5ms`). Use this only for MVP demonstration flow:

```powershell
$VALIDATION_ID = "<validation_id_from_step_1>"
docker compose exec -T db psql -U solarone -d validation_engine -c "UPDATE validations SET status='approved', confidence_score=100.0 WHERE validation_id='$VALIDATION_ID';"
```

---

## 3) Issue HEC (`POST /hec/issue`)
```powershell
$BASE = "http://localhost:8000"
$VALIDATION_ID = "<validation_id_from_step_1>"

$hecIssue = Invoke-RestMethod -Method Post -Uri "$BASE/hec/issue" -ContentType "application/json" -Body (@{
  validation_id = $VALIDATION_ID
} | ConvertTo-Json)

$hecIssue | ConvertTo-Json -Depth 10
```

Expected:
- `status`: `registered`
- `hec_id`: non-null
- `ipfs_json_cid`: non-null
- `registry_tx_hash`: non-null

---

## 4) Verify HEC (`GET /hec/verify/{hec_id}`)
```powershell
$HEC_ID = "<hec_id_from_step_3>"
Invoke-RestMethod "$BASE/hec/verify/$HEC_ID" | ConvertTo-Json -Depth 10
```

Expected: `verified=true`, `match=true`.

---

## 5) Verify on-chain registry (`GET /hec/onchain/{hec_id}`)
```powershell
Invoke-RestMethod "$BASE/hec/onchain/$HEC_ID" | ConvertTo-Json -Depth 10
```

Expected: `exists=true`.

---

## 6) Create lot (`POST /lots/create`)
```powershell
$lot = Invoke-RestMethod -Method Post -Uri "$BASE/lots/create" -ContentType "application/json" -Body (@{
  hec_ids = @($HEC_ID)
  name = "MVP Lot Sample"
  description = "Created from smoke sample"
  price_per_kwh = 0.5
} | ConvertTo-Json)

$lot | ConvertTo-Json -Depth 10
```

Expected:
- `lot_id`: non-null
- `backing_complete=true`
- `status=open`

---

## 7) Register buyer (`POST /marketplace/register`)
```powershell
$email = "sample_$([guid]::NewGuid().ToString('N').Substring(0,8))@test.com"
$register = Invoke-RestMethod -Method Post -Uri "$BASE/marketplace/register" -ContentType "application/json" -Body (@{
  email = $email
  name = "Sample Buyer"
  password = "test123"
} | ConvertTo-Json)

$register | ConvertTo-Json -Depth 10
$TOKEN = $register.token
```

Expected: `token` returned.

---

## 8) Buy from lot (`POST /marketplace/buy`)
```powershell
$headers = @{ Authorization = "Bearer $TOKEN" }

$buy = Invoke-RestMethod -Method Post -Uri "$BASE/marketplace/buy" -Headers $headers -ContentType "application/json" -Body (@{
  lot_id = $lot.lot_id
  quantity = 1
} | ConvertTo-Json)

$buy | ConvertTo-Json -Depth 10
```

Expected:
- `tx_id`: non-null
- `status=completed`
- `wallet_hec_after >= 1`

---

## 9) Burn (`POST /burn`)
```powershell
$burn = Invoke-RestMethod -Method Post -Uri "$BASE/burn" -Headers $headers -ContentType "application/json" -Body (@{
  quantity = 1
  reason = "voluntary"
} | ConvertTo-Json)

$burn | ConvertTo-Json -Depth 10
```

Expected:
- `burn_id`: non-null
- `status=burned`
- `registry_tx_hash`: non-null

---

## 10) Optional proof pack fields to capture
- `validation_id`
- `hec_id`
- `lot_id`
- `tx_id` (buy)
- `burn_id`
- `registry_tx_hash` (HEC and burn)
- `/hec/verify` response with `verified=true`
- `/hec/onchain` response with `exists=true`

These fields are enough to prove the full MVP lifecycle worked.
