#!/bin/bash
set -e

echo "Waiting for PostgreSQL + TimescaleDB..."
until python -c "
import psycopg2, os
conn = psycopg2.connect(
    host=os.getenv('POSTGRES_HOST', 'db'),
    port=os.getenv('POSTGRES_PORT', '5432'),
    user=os.getenv('POSTGRES_USER', 'solarone'),
    password=os.getenv('POSTGRES_PASSWORD', 'solarone_secret'),
    dbname=os.getenv('POSTGRES_DB', 'validation_engine'),
)
conn.close()
print('PostgreSQL ready')
" 2>/dev/null; do
    echo "  ...database still unavailable, retrying in 2s"
    sleep 2
done

if [ "$(echo "${SOA_ENABLE_INGEST:-false}" | tr '[:upper:]' '[:lower:]')" = "true" ]; then
    echo "Waiting for MariaDB..."
    until python -c "
import os
import pymysql
conn = pymysql.connect(
    host=os.getenv('SOA_MYSQL_HOST', 'mariadb'),
    port=int(os.getenv('SOA_MYSQL_PORT', '3306')),
    user=os.getenv('SOA_MYSQL_USER', 'solarone'),
    password=os.getenv('SOA_MYSQL_PASSWORD', 'solarone_secret'),
    database=os.getenv('SOA_MYSQL_DB', 'soa_sos'),
    connect_timeout=3,
)
conn.close()
print('MariaDB ready')
" 2>/dev/null; do
        echo "  ...mariadb still unavailable, retrying in 2s"
        sleep 2
    done
fi

echo "Running Alembic migrations..."
alembic upgrade head

echo "Running seed..."
python -m scripts.seed

echo "Starting Validation Engine..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
