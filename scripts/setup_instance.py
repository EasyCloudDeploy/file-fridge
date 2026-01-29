import os
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.database import init_db
from app.services.identity_service import identity_service


def setup():
    if len(sys.argv) < 2:
        print("Usage: setup_instance.py <db_path>")
        sys.exit(1)

    db_path = sys.argv[1]
    os.environ["DATABASE_PATH"] = db_path

    # Force re-initialization of engine for this process
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import app.database
    app.database.engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    app.database.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=app.database.engine)

    init_db()
    db = app.database.SessionLocal()
    try:
        fingerprint = identity_service.get_instance_fingerprint(db)
        pub_signing = identity_service.get_signing_public_key_str(db)
        pub_kx = identity_service.get_kx_public_key_str(db)

        print(f"FINGERPRINT:{fingerprint}")
        print(f"PUB_SIGNING:{pub_signing}")
        print(f"PUB_KX:{pub_kx}")
    finally:
        db.close()

if __name__ == "__main__":
    setup()
