import asyncio
import hashlib
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

import aiofiles
import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import (
    FileInventory,
    MonitoredPath,
    RemoteConnection,
    StorageType,
    TrustStatus,
)

# We'll use absolute paths to avoid confusion
BASE_DIR = Path("./file-fridge").absolute()
TEST_DIR = BASE_DIR / "test_run"
INSTANCE_A_DIR = TEST_DIR / "instance_a"
INSTANCE_B_DIR = TEST_DIR / "instance_b"

PORT_A = 8001
PORT_B = 8002


async def cleanup():
    # Kill any processes on PORT_A or PORT_B
    for port in [PORT_A, PORT_B]:
        try:
            cmd = ["lsof", "-ti", f":{port}"]
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await proc.communicate()
            result = stdout.decode().strip()
            if result:
                for pid in result.splitlines():
                    print(f"Killing process {pid} on port {port}")
                    kill_proc = await asyncio.create_subprocess_shell(f"kill -9 {pid}")
                    await kill_proc.wait()
        except Exception:
            pass

    if TEST_DIR.exists():
        shutil.rmtree(TEST_DIR)
    TEST_DIR.mkdir(parents=True)
    INSTANCE_A_DIR.mkdir()
    (INSTANCE_A_DIR / "data").mkdir()
    (INSTANCE_A_DIR / "hot").mkdir()
    INSTANCE_B_DIR.mkdir()
    (INSTANCE_B_DIR / "data").mkdir()
    (INSTANCE_B_DIR / "hot").mkdir()


async def create_dummy_1gb_file(path: Path):
    print(f"Creating 1GB dummy file at {path}...")
    chunk = os.urandom(1024 * 1024)
    async with aiofiles.open(path, "wb") as f:
        for _ in range(1024):
            await f.write(chunk)
    print("Dummy file created.")


async def run_setup(db_path):
    cmd = [str(BASE_DIR / ".venv" / "bin" / "python3"), "scripts/setup_instance.py", str(db_path)]
    proc = await asyncio.create_subprocess_exec(
        *cmd, cwd=BASE_DIR, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, _ = await proc.communicate()
    result = stdout.decode()
    data = {}
    for line in result.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            data[k] = v
    return data


async def stream_logs_async(stream, prefix):
    async for line in stream:
        if line:
            print(f"{prefix}: {line.decode().strip()}")


async def run_simulation():
    await cleanup()

    file_a = INSTANCE_A_DIR / "hot" / "large_file.dat"
    await create_dummy_1gb_file(file_a)

    # 1. Initialize Identities in separate processes
    print("Initializing Instance A...")
    id_a = await run_setup(INSTANCE_A_DIR / "data" / "file_fridge_a.db")
    print(f"Identity A: {id_a['FINGERPRINT']}")

    print("Initializing Instance B...")
    id_b = await run_setup(INSTANCE_B_DIR / "data" / "file_fridge_b.db")
    print(f"Identity B: {id_b['FINGERPRINT']}")

    # 2. Setup Mutual Trust and Paths (Direct DB write)
    # DB A setup
    engine_a = create_engine(f"sqlite:///{INSTANCE_A_DIR / 'data' / 'file_fridge_a.db'}")
    session_a_factory = sessionmaker(bind=engine_a)
    db_a = session_a_factory()

    conn_b = RemoteConnection(
        name="Instance B",
        url=f"http://localhost:{PORT_B}",
        remote_fingerprint=id_b["FINGERPRINT"],
        remote_ed25519_public_key=id_b["PUB_SIGNING"],
        remote_x25519_public_key=id_b["PUB_KX"],
        trust_status=TrustStatus.TRUSTED,
    )
    db_a.add(conn_b)
    path_a = MonitoredPath(name="Hot A", source_path=str(INSTANCE_A_DIR / "hot"))
    db_a.add(path_a)
    db_a.flush()

    # Compute checksum with proper file handle management
    async with aiofiles.open(file_a, "rb") as f:
        data = await f.read(4096)
        checksum = hashlib.sha256(data).hexdigest()

    file_obj = FileInventory(
        path_id=path_a.id,
        file_path=str(file_a),
        file_size=1024 * 1024 * 1024,
        file_mtime=datetime.fromtimestamp(file_a.stat().st_mtime, tz=timezone.utc),
        storage_type=StorageType.HOT,
        checksum=checksum,
    )
    db_a.add(file_obj)
    db_a.commit()
    file_id_a = file_obj.id
    conn_b_id = conn_b.id
    db_a.close()

    # DB B setup
    engine_b = create_engine(f"sqlite:///{INSTANCE_B_DIR / 'data' / 'file_fridge_b.db'}")
    session_b_factory = sessionmaker(bind=engine_b)
    db_b = session_b_factory()
    conn_a = RemoteConnection(
        name="Instance A",
        url=f"http://localhost:{PORT_A}",
        remote_fingerprint=id_a["FINGERPRINT"],
        remote_ed25519_public_key=id_a["PUB_SIGNING"],
        remote_x25519_public_key=id_a["PUB_KX"],
        trust_status=TrustStatus.TRUSTED,
    )
    db_b.add(conn_a)
    path_b = MonitoredPath(name="Hot B", source_path=str(INSTANCE_B_DIR / "hot"))
    db_b.add(path_b)
    db_b.commit()
    remote_path_id_b = path_b.id
    db_b.close()

    print("Mutual trust and paths configured.")

    # 3. Start Instance B (Receiver)
    print("Starting Instance B (Receiver) uvicorn...")
    env_b = {
        **os.environ,
        "DATABASE_PATH": str(INSTANCE_B_DIR / "data" / "file_fridge_b.db"),
        "SECRET_KEY": "test-secret-b",
        "FF_INSTANCE_URL": f"http://localhost:{PORT_B}",
        "LOG_LEVEL": "DEBUG",
        "PYTHONUNBUFFERED": "1",
    }

    proc_b = await asyncio.create_subprocess_exec(
        str(BASE_DIR / ".venv" / "bin" / "uvicorn"),
        "app.main:app",
        "--host",
        "localhost",
        "--port",
        str(PORT_B),
        cwd=BASE_DIR,
        env=env_b,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    # Store reference to task to avoid it being garbage collected
    log_task_b = asyncio.create_task(stream_logs_async(proc_b.stdout, "[B]"))

    # Wait for B to be ready
    print("Waiting for Instance B to be ready...")
    ready = False
    for _ in range(30):
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"http://localhost:{PORT_B}/api/v1/remote/identity")
                if resp.status_code == 200:
                    ready = True
                    break
        except Exception:
            pass
        await asyncio.sleep(1)

    if not ready:
        print("Instance B failed to start.")
        proc_b.terminate()
        await proc_b.wait()
        return

    # 4. Run Transfer in a separate process
    print("Initiating transfer from A to B via sub-process...")
    # Create a small script to trigger and run the transfer
    transfer_script = TEST_DIR / "trigger_transfer.py"
    async with aiofiles.open(transfer_script, "w") as f:
        await f.write(
            f"""
import os
import asyncio
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, "{BASE_DIR}")

from app.database import init_db, SessionLocal
from app.services.remote_transfer_service import remote_transfer_service

async def main():
    os.environ["DATABASE_PATH"] = "{INSTANCE_A_DIR / 'data' / 'file_fridge_a.db'}"
    db = SessionLocal()
    try:
        job = remote_transfer_service.create_transfer_job(
            db, {file_id_a}, {conn_b_id}, {remote_path_id_b}
        )
        print(f"Transfer job {{job.id}} created.")
        await remote_transfer_service.run_transfer(job.id)
    finally:
        db.close()

if __name__ == "__main__":
    asyncio.run(main())
"""
        )

    env_a = {
        **os.environ,
        "DATABASE_PATH": str(INSTANCE_A_DIR / "data" / "file_fridge_a.db"),
        "SECRET_KEY": "test-secret-a",
        "FF_INSTANCE_URL": f"http://localhost:{PORT_A}",
        "LOG_LEVEL": "DEBUG",
        "PYTHONUNBUFFERED": "1",
    }

    proc_a = await asyncio.create_subprocess_exec(
        str(BASE_DIR / ".venv" / "bin" / "python3"),
        str(transfer_script),
        cwd=BASE_DIR,
        env=env_a,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    # Store reference to task to avoid it being garbage collected
    log_task_a = asyncio.create_task(stream_logs_async(proc_a.stdout, "[A]"))

    # Wait for A to finish
    await proc_a.wait()

    print("Transfer process finished. Verifying result...")
    await asyncio.sleep(2)  # Buffer for B to finalize

    file_b = INSTANCE_B_DIR / "hot" / "large_file.dat"
    if file_b.exists():
        print(f"SUCCESS: File received at {file_b}")
        print(f"Final size: {file_b.stat().st_size} bytes")
    else:
        print("FAILURE: File not found at destination.")
        fftmp = file_b.with_suffix(file_b.suffix + ".fftmp")
        if fftmp.exists():
            print(f"Partial file found: {fftmp.stat().st_size} bytes")

    # Clean up
    proc_b.terminate()
    await proc_b.wait()
    # Cancel log tasks
    log_task_b.cancel()
    log_task_a.cancel()


if __name__ == "__main__":
    asyncio.run(run_simulation())
