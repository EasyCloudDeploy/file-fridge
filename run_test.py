import os
os.environ['SECRET_KEY'] = 'dummy_key'
import pytest
import sys
sys.exit(pytest.main(['-v', 'tests/services/test_relocation_manager.py']))
