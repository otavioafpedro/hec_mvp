#!/bin/bash
set -e

echo "⏳ Aguardando PostgreSQL + TimescaleDB..."
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
print('DB ready')
" 2>/dev/null; do
    echo "  ...banco ainda não disponível, tentando novamente em 2s"
    sleep 2
done

echo "✅ Banco conectado!"

echo "🔄 Executando migrations (Alembic)..."
alembic upgrade head

echo "🌱 Executando seed..."
python -m scripts.seed

echo "🚀 Iniciando Validation Engine..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
