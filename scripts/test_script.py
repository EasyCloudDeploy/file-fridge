import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.services.scan_progress import scan_progress_manager

print(dir(scan_progress_manager))
