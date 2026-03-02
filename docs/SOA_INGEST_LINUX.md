# SOA Ingest Setup (Linux)

Objetivo: subir MariaDB + PostgreSQL/TimescaleDB com os schemas da pasta `sql_hec_soa` e habilitar a primeira API de ingestao de dados de inversor.

## 1. Pre-requisitos no servidor Linux

```bash
sudo apt-get update
sudo apt-get install -y docker.io docker-compose-plugin
sudo systemctl enable --now docker
```

## 2. Subir stack com schema automatico

No diretorio do projeto:

```bash
cd /opt/hec_mvp/hec_mvp
cp .env.example .env
docker compose down -v
docker compose up --build -d
```

Importante:
- `docker compose down -v` remove volumes para forcar reexecucao dos scripts de init.
- `sql_hec_soa/mysql_schema.sql` e `sql_hec_soa/postgres_timeseries.sql` rodam automaticamente na primeira inicializacao.
- `sql_hec_soa/mysql_seed_minimal.sql` cria org/site/device minimo para smoke test.

## 3. Validar bancos e API

```bash
docker compose ps
curl -s http://localhost:8000/health | jq
```

Esperado no `/health`:
- `soa_ingest_enabled: true`
- `checks.soa_mariadb.status: ok`
- `checks.soa_timeseries.inverter_telemetry_table: ok`

## 4. Enviar telemetria de inversor (primeira API)

Payload de exemplo (usa o device do seed minimo):

```bash
curl -X POST http://localhost:8000/soa/v1/inverter-telemetry \
  -H "Content-Type: application/json" \
  -d '{
    "device_uuid": "00000000-0000-0000-0000-000000000301",
    "timestamp": "2026-03-02T12:00:00Z",
    "power_ac_w": 5200.5,
    "power_dc_w": 5450.1,
    "energy_today_wh": 18400,
    "energy_total_wh": 9632400,
    "voltage_ac_v": 220.2,
    "current_ac_a": 23.6,
    "frequency_hz": 59.98,
    "efficiency_pct": 95.4,
    "temperature_c": 42.1,
    "status_code": 0,
    "error_code": 0,
    "is_online": true,
    "data_quality": 98
  }'
```

Resposta esperada:
- `status: accepted`
- `device_sync_ok: true` (ou `false` se falhar apenas o update de heartbeat no MariaDB)

## 5. Smoke SQL rapido

MariaDB:

```bash
docker compose exec mariadb mariadb -usolarone -psolarone_secret soa_sos \
  -e "SELECT id, uuid, site_id, status, last_seen_at FROM devices;"
```

PostgreSQL:

```bash
docker compose exec db psql -U solarone -d validation_engine \
  -c "SELECT ts, device_id, site_id, power_ac_w, energy_today_wh FROM inverter_telemetry ORDER BY ts DESC LIMIT 5;"
```
