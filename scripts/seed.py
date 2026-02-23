"""
Seed script — Insere 1 planta de exemplo e gera par de chaves ECDSA para testes.

Uso:
  python -m scripts.seed
"""
import sys
import os
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime
from app.db.session import SessionLocal
from app.models.models import Plant
from app.security import generate_ecdsa_keypair


SEED_PLANT_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


def seed():
    db = SessionLocal()
    try:
        # Verifica se já existe
        existing = db.query(Plant).filter(Plant.plant_id == SEED_PLANT_ID).first()
        if existing:
            print(f"[SEED] Planta já existe: {existing.name} ({existing.plant_id})")
        else:
            plant = Plant(
                plant_id=SEED_PLANT_ID,
                name="Usina Solar Piloto ABSOLAR — São Paulo",
                absolar_id="ABSOLAR-SP-PILOT-001",
                owner_name="Solar One Demonstração",
                lat=-23.5505,
                lng=-46.6333,
                capacity_kw=75.0,
                status="active",
                inverter_brand="Growatt",
                inverter_model="MIN 6000TL-X",
                commissioning_date=datetime(2025, 6, 15),
            )
            db.add(plant)
            db.commit()
            print(f"[SEED] Planta criada: {plant.name} ({plant.plant_id})")

        # Gera par de chaves ECDSA para testes
        private_pem, public_pem = generate_ecdsa_keypair()
        print("\n[SEED] Par de chaves ECDSA (secp256k1) para testes:")
        print("=" * 60)
        print("[PRIVATE KEY]")
        print(private_pem)
        print("[PUBLIC KEY]")
        print(public_pem)
        print("=" * 60)
        print(f"\n[SEED] Plant ID para testes: {SEED_PLANT_ID}")
        print("[SEED] Seed concluído com sucesso!")

    except Exception as e:
        db.rollback()
        print(f"[SEED] Erro: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    seed()
