import os
os.environ['SECRET_KEY'] = 'dummy_key'

from app.database import engine, Base
import app.models

Base.metadata.create_all(bind=engine)

import pytest
import sys
sys.exit(pytest.main(['-v', 'tests/services/test_relocation_manager.py::TestRelocationManager::test_process_task_success']))
