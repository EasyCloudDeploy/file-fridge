import os
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from alembic.config import Config

from alembic import command
from app.database import Base, engine

# Remove test DB to start fresh
if os.path.exists("data/file_fridge.db"):
    os.remove("data/file_fridge.db")

# Create tables
Base.metadata.create_all(engine)

# Stamp head
alembic_cfg = Config("alembic.ini")
command.stamp(alembic_cfg, "head")
