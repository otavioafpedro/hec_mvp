# Validation Engine — Solar One HUB / ABSOLAR

Motor de validação de dados de geração solar distribuída para o Ecossistema Solar One.
Implementa a **Fortaleza Lógica Camada 1** (Pre-commitment Hash ECDSA) com ingestão segura
de telemetria, anti-replay e integridade SHA-256.

## Stack

| Componente | Versão |
|---|---|
| Python | 3.11 |
| FastAPI | 0.115.x |
| PostgreSQL | 15 |
| TimescaleDB | latest |
| SQLAlchemy | 2.0.x |
| Alembic | 1.14.x |
| Docker + Compose | latest |
| Cryptography (ECDSA) | 44.x |

## Tabelas

- **plants** — Usinas e sistemas de geração solar
- **telemetry** — Hypertable TimescaleDB (séries temporais dos inversores)
- **validations** — Resultado validação SOS/SENTINEL por período
- **hec_certificates** — Ativo digital: certificado de energia (NFT)
- **hec_lots** — Agrupamento de HECs para comercialização em lote
- **used_nonces** — Anti-replay: nonces utilizados (TTL 60s)

## Execução

```bash
# 1. Subir tudo (banco + API)
docker-compose up --build

# 2. Verificar saúde
curl http://localhost:8000/health

# 3. Acessar documentação interativa
open http://localhost:8000/docs
```

O entrypoint executa automaticamente:
1. Aguarda o banco ficar disponível
2. Roda migrations (Alembic)
3. Executa seed (1 planta + gera par ECDSA)
4. Inicia uvicorn na porta 8000

## Endpoints

### `GET /health`
Retorna status do serviço e conectividade com banco + TimescaleDB.

### `POST /telemetry`
Ingestão segura de telemetria solar com assinatura ECDSA.

**Payload:**
```json
{
  "plant_id": "00000000-0000-0000-0000-000000000001",
  "timestamp": "2026-02-23T14:30:00Z",
  "power_kw": 5.5,
  "energy_kwh": 12.3,
  "signature": "<hex da assinatura ECDSA>",
  "public_key": "<PEM da chave pública EC secp256k1>",
  "nonce": "<string única de 8-64 chars>"
}
```

**Pipeline de segurança:**
1. Verifica existência da planta
2. Monta payload canônico (determinístico, chaves ordenadas)
3. Valida assinatura ECDSA (secp256k1) sobre o payload canônico
4. Verifica nonce anti-replay (janela de 60 segundos)
5. Gera SHA-256 do payload para integridade
6. Persiste no banco (hypertable TimescaleDB)

**Respostas:**
- `201` — Telemetria aceita
- `401` — Assinatura ECDSA inválida
- `404` — Planta não encontrada
- `409` — Replay attack detectado (nonce repetido)
- `422` — Payload inválido

## Testes

```bash
# Instalar deps localmente
pip install -r requirements.txt

# Rodar todos os testes (usa SQLite em memória)
pytest

# Rodar só testes unitários de segurança (sem banco)
pytest tests/test_telemetry.py -k "TestCanonical or TestSHA256 or TestECDSA"

# Rodar testes de integração do endpoint
pytest tests/test_telemetry.py -k "TestTelemetryEndpoint"
```

### Cenários cobertos:
- ✅ Assinatura ECDSA válida → 201
- ❌ Assinatura corrompida → 401
- ❌ Chave pública errada → 401
- ❌ Replay attack (mesmo nonce) → 409
- ✅ Nonces diferentes aceitos
- ❌ Planta inexistente → 404
- ❌ Nonce curto demais → 422
- ❌ Potência negativa → 422
- ✅ SHA-256 do payload verificável localmente

## Exemplo: Enviar telemetria via Python

```python
import uuid
from app.security import generate_ecdsa_keypair, sign_payload, canonical_payload

# Gerar chaves (uma vez)
private_pem, public_pem = generate_ecdsa_keypair()

# Montar payload
plant_id = "00000000-0000-0000-0000-000000000001"
timestamp = "2026-02-23T14:30:00Z"
power_kw = 5.5
energy_kwh = 12.3
nonce = uuid.uuid4().hex[:32]

# Assinar
canon = canonical_payload(plant_id, timestamp, power_kw, energy_kwh, nonce)
signature = sign_payload(private_pem, canon)

# Enviar
import httpx
r = httpx.post("http://localhost:8000/telemetry", json={
    "plant_id": plant_id,
    "timestamp": timestamp,
    "power_kw": power_kw,
    "energy_kwh": energy_kwh,
    "signature": signature,
    "public_key": public_pem,
    "nonce": nonce,
})
print(r.json())
```

## Estrutura do Projeto

```
validation-engine/
├── app/
│   ├── api/
│   │   ├── health.py          # GET /health
│   │   └── telemetry.py       # POST /telemetry (ECDSA + anti-replay)
│   ├── db/
│   │   └── session.py         # SQLAlchemy engine + session
│   ├── models/
│   │   └── models.py          # 6 tabelas (plants, telemetry, validations, etc.)
│   ├── schemas/
│   │   └── telemetry.py       # Pydantic request/response
│   ├── config.py              # Settings (pydantic-settings)
│   ├── main.py                # FastAPI app
│   └── security.py            # ECDSA, SHA-256, nonce anti-replay
├── alembic/
│   ├── versions/
│   │   └── 001_initial_schema.py
│   └── env.py
├── scripts/
│   └── seed.py                # Seed: 1 planta + gera chaves ECDSA
├── tests/
│   ├── conftest.py            # Fixtures (SQLite, TestClient, ECDSA keys)
│   └── test_telemetry.py      # 14 testes (unitários + integração)
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── alembic.ini
├── pytest.ini
├── entrypoint.sh
└── README.md
```
