import asyncio, os, sys
sys.path.insert(0, "/Users/martino/repos/file-fridge")
os.environ["DATABASE_PATH"] = "/Users/martino/repos/file-fridge/remote_test_run/instance_a/data/file_fridge_a.db"
os.environ["SECRET_KEY"] = "dummy_key_for_remote_testing"
from app.services.remote_transfer_service import remote_transfer_service
async def main(): await remote_transfer_service.run_transfer(1)
if __name__ == "__main__": asyncio.run(main())