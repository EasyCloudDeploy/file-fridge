import os
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ["SECRET_KEY"] = "dummy_key"

from app.database import Base, engine

Base.metadata.create_all(bind=engine)

import sys

import pytest

sys.exit(pytest.main(["-v", "tests/services/test_relocation_manager.py::TestRelocationManager::test_process_task_success"]))
