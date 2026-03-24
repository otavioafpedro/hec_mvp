# Deploy Railway em Camadas (HEC MVP)

Este projeto agora suporta deploy em multiplos servicos no Railway usando o mesmo repositório.

## Arquitetura sugerida

1. `hec-api-core` (Web): ingestao/validacao/certificados
2. `hec-ds-cross-validation` (Worker): agregacao DS de `inverter_telemetry` para `energy_intervals`
3. `hec-blockchain-mint` (Worker): registra HECs pendentes on-chain
4. `hec-blockchain-burn` (Worker): registra burns pendentes on-chain
5. `hec-consumer-api` (Web): marketplace/lotes/burn para consumidor final

## Start command unico

Todos os servicos usam o mesmo start command:

```bash
python -m app.launcher
```

Cada servico diferencia o comportamento via env `SERVICE_LAYER`.

Valores validos:
- `api`
- `ds_cross_validation`
- `blockchain_mint`
- `blockchain_burn`
- `consumer`

## Variaveis de ambiente (Railway)

Minimo comum para todos:

```bash
SERVICE_LAYER=api
POSTGRES_DSN=${{Postgres.DATABASE_URL}}
SOA_ENABLE_INGEST=true
SOA_MYSQL_DSN=${{MariaDB.DATABASE_URL}}
SOA_TIMESERIES_DSN=${{Timescale.DATABASE_URL}}
WORKER_POLL_SECONDS=20
WORKER_BATCH_SIZE=50
DS_LOOKBACK_HOURS=24
```

Notas:
- `POSTGRES_DSN` aponta para o banco legado (`validation_engine`) usado por API/marketplace/hec/burn.
- Identidade do usuario (`users`, `wallets`, `consumer_profiles`, `user_role_bindings`, `generator_profiles`) vive neste PostgreSQL.
- `SOA_TIMESERIES_DSN` aponta para o banco de series temporais (`inverter_telemetry`, `energy_intervals`, etc).
- `SOA_MYSQL_DSN` aponta para o banco transacional SOA (tabelas `sites`, `devices`, etc).
- O MariaDB atual nao recebe espelho de usuarios; ele guarda somente entidades operacionais do SOA.
- Se preferir, pode usar `POSTGRES_HOST/PORT/USER/PASSWORD/DB` em vez de `POSTGRES_DSN`.

## Configuracao por servico

### 1) hec-api-core (Web)
- `SERVICE_LAYER=api`
- `RUN_DB_MIGRATIONS_ON_BOOT=true` (somente neste servico)
- Porta HTTP: Railway injeta `PORT` automaticamente.

### 2) hec-ds-cross-validation (Worker)
- `SERVICE_LAYER=ds_cross_validation`
- `RUN_DB_MIGRATIONS_ON_BOOT=false`

### 3) hec-blockchain-mint (Worker)
- `SERVICE_LAYER=blockchain_mint`
- `RUN_DB_MIGRATIONS_ON_BOOT=false`

### 4) hec-blockchain-burn (Worker)
- `SERVICE_LAYER=blockchain_burn`
- `RUN_DB_MIGRATIONS_ON_BOOT=false`

### 5) hec-consumer-api (Web)
- `SERVICE_LAYER=consumer`
- `RUN_DB_MIGRATIONS_ON_BOOT=false`

## Ordem recomendada de deploy

1. Criar plugins DB no Railway (Postgres legado, Postgres timeseries, MariaDB)
2. Rodar schema/tabelas (como voce ja fez)
3. Subir `hec-api-core`
4. Validar `GET /health`
5. Subir workers (`ds`, `mint`, `burn`)
6. Subir `hec-consumer-api`

## Smoke test rapido

### API core

```bash
curl https://<api-core>/health
curl https://<api-core>/integrations/status
curl -X POST https://<api-core>/generator-onboarding/register \
  -H "Content-Type: application/json" \
  -d '{
    "email":"gerador.demo@solarone.com",
    "name":"Gerador Demo",
    "password":"demo123",
    "person_type":"PF",
    "document_id":"12345678901",
    "attribute_assignment_accepted":true,
    "plant":{
      "name":"Usina Demo",
      "lat":-23.55,
      "lng":-46.63,
      "capacity_kw":75
    },
    "inverter_connection":{
      "provider_name":"growatt",
      "integration_mode":"direct_api",
      "consent_accepted":true
    }
  }'

# conta consumidora existente vira gerador mantendo o mesmo user/token
curl -X POST https://<api-core>/generator-onboarding/activate \
  -H "Authorization: Bearer <token-consumidor>" \
  -H "Content-Type: application/json" \
  -d '{
    "person_type":"PF",
    "document_id":"98765432100",
    "attribute_assignment_accepted":true,
    "plant":{
      "name":"Usina Existente",
      "lat":-23.55,
      "lng":-46.63,
      "capacity_kw":55
    },
    "inverter_connection":{
      "provider_name":"sungrow",
      "integration_mode":"vendor_partner",
      "consent_accepted":true
    }
  }'
```

### Ingestao inverter (SOA)

```bash
curl -X POST https://<api-core>/soa/v1/inverter-telemetry \
  -H "Content-Type: application/json" \
  -d '{
    "device_uuid":"00000000-0000-0000-0000-000000000301",
    "timestamp":"2026-03-03T12:00:00Z",
    "power_ac_w":5200.5
  }'
```

### Consumer API

```bash
curl https://<consumer-api>/health
```

## Observabilidade minima

Nos workers, procure logs:
- `ds_cross_validation_batch_processed`
- `mint_batch_processed`
- `burn_batch_processed`

Se houver falha, o worker loga `worker_cycle_failed` com stacktrace.

## Integracoes externas portadas do SOA gateway

As seguintes fontes agora estao no `hec_mvp` em `app/integrations/*`:
- Solcast (S1)
- NASA POWER fallback (S2 satelite)
- INMET + OpenWeather (S3 clima)
- Electricity Maps (ESG)

Variaveis opcionais no Railway:

```bash
SOLCAST_API_KEY=
COPERNICUS_CLIENT_ID=
COPERNICUS_CLIENT_SECRET=
OPENWEATHER_API_KEY=
ELECTRICITY_MAPS_KEY=
DEFAULT_GRID_ZONE=BR-CS
DS_ENABLE_EXTERNAL_FETCH=true
```
