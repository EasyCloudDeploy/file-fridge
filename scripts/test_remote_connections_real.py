import asyncio
import hashlib
import json
import os
import shutil
import sys
import uuid
from pathlib import Path

import aiofiles
import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Add project root to sys.path
BASE_DIR = Path(__file__).parent.parent.absolute()
sys.path.insert(0, str(BASE_DIR))

from app.models import (
    ColdStorageLocation,
    InstanceMetadata,
    MonitoredPath,
    RemoteConnection,
    TrustStatus,
    User,
)
from app.security import hash_password

# Use absolute paths
TEST_DIR = BASE_DIR / "remote_test_run"
INSTANCE_A_DIR = TEST_DIR / "instance_a"
INSTANCE_B_DIR = TEST_DIR / "instance_b"

PORT_A = 8001
PORT_B = 8002

# Shared secret key for consistency in tests
SECRET_KEY = "dummy_key_for_remote_testing"

def parse_api_json(resp):
    """Robustly parse NDJSON or standard JSON from an API response that might contain noise."""
    text = resp.text.strip()
    if not text:
        return []

    lines = text.splitlines()
    results = []
    for line in lines:
        start = line.find("{")
        end = line.rfind("}")
        if start != -1 and end != -1:
            try:
                results.append(json.loads(line[start:end+1]))
            except Exception:
                continue
    return results

async def cleanup():
    print("Cleaning up processes and directories...")
    for port in [PORT_A, PORT_B]:
        try:
            if sys.platform == "darwin" or sys.platform == "linux":
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
        except Exception as e:
            print(f"Cleanup error for port {port}: {e}")

    if TEST_DIR.exists():
        shutil.rmtree(TEST_DIR)
    TEST_DIR.mkdir(parents=True)

    for inst_dir in [INSTANCE_A_DIR, INSTANCE_B_DIR]:
        inst_dir.mkdir()
        (inst_dir / "data").mkdir()
        (inst_dir / "hot").mkdir()
        (inst_dir / "cold").mkdir()

async def create_test_file(path: Path, size_mb: int = 10):
    print(f"Creating {size_mb}MB test file at {path}...")
    chunk = os.urandom(1024 * 1024)
    async with aiofiles.open(path, "wb") as f:
        for _ in range(size_mb):
            await f.write(chunk)

    sha256_hash = hashlib.sha256()
    async with aiofiles.open(path, "rb") as f:
        while True:
            data = await f.read(4096)
            if not data:
                break
            sha256_hash.update(data)
    return sha256_hash.hexdigest()

async def run_identity_setup(db_path):
    cmd = [sys.executable, "scripts/setup_instance.py", str(db_path)]
    env = {**os.environ, "SECRET_KEY": SECRET_KEY}
    proc = await asyncio.create_subprocess_exec(
        *cmd, cwd=BASE_DIR, env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        print(f"Setup failed for {db_path}: {stderr.decode()}")
        return None

    result = stdout.decode()
    data = {}
    for line in result.splitlines():
        if ":" in line:
            parts = line.split(":", 1)
            if len(parts) == 2:
                k, v = parts
                data[k.strip()] = v.strip()
    return data

async def stream_logs(stream, prefix):
    async for line in stream:
        if line:
            text = line.decode().strip()
            if "INFO" in text or "WARNING" in text or "ERROR" in text:
                print(f"{prefix}: {text}")

async def start_instance(name, port, inst_dir):
    env = {
        **os.environ,
        "DATABASE_PATH": str(inst_dir / "data" / f"file_fridge_{name.lower()}.db"),
        "SECRET_KEY": SECRET_KEY,
        "FF_INSTANCE_URL": f"http://localhost:{port}",
        "LOG_LEVEL": "ERROR",
        "PYTHONUNBUFFERED": "1",
        "TESTING": "true",
        "DISABLE_RATE_LIMIT": "true",
        "DISABLE_SCHEDULER": "true"
    }

    proc = await asyncio.create_subprocess_exec(
        "uv", "run", "uvicorn", "app.main:app",
        "--host", "localhost",
        "--port", str(port),
        cwd=BASE_DIR,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )

    log_task = asyncio.create_task(stream_logs(proc.stdout, f"[{name}]"))

    ready = False
    for _ in range(30):
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"http://localhost:{port}/health")
                if resp.status_code == 200:
                    ready = True
                    break
        except Exception:
            pass
        await asyncio.sleep(1)

    if not ready:
        print(f"Instance {name} failed to start.")
        proc.terminate()
        await proc.wait()
        return None, None

    return proc, log_task

async def run_test():
    await cleanup()

    print("\n--- Step 0: Pre-creating test files ---")
    file_a_path = INSTANCE_A_DIR / "hot" / "push_test.dat"
    checksum_a = await create_test_file(file_a_path, 5)
    file_b_path = INSTANCE_B_DIR / "hot" / "pull_test.dat"
    checksum_b = await create_test_file(file_b_path, 3)

    print("\n--- Step 1: Initializing Identities ---")
    db_a_path = INSTANCE_A_DIR / "data" / "file_fridge_a.db"
    db_b_path = INSTANCE_B_DIR / "data" / "file_fridge_b.db"
    id_a = await run_identity_setup(db_a_path)
    id_b = await run_identity_setup(db_b_path)
    if not id_a or not id_b: return

    print("\n--- Step 2: Setting up Trust, Paths, and User ---")

    engine_a = create_engine(f"sqlite:///{db_a_path}")
    SessionA = sessionmaker(bind=engine_a)
    with SessionA() as session:
        session.add(InstanceMetadata(instance_uuid=str(uuid.uuid4()), instance_url=f"http://localhost:{PORT_A}", instance_name="Instance A"))
        admin = User(username="admin", password_hash=hash_password("password123"), is_active=True, roles=["admin"])
        session.add(admin)
        cold_a = ColdStorageLocation(name="Cold A", path=str(INSTANCE_A_DIR / "cold"), caution_threshold_percent=20, critical_threshold_percent=10)
        session.add(cold_a)
        conn_b = RemoteConnection(
            name="Instance B", url=f"http://localhost:{PORT_B}",
            remote_fingerprint=id_b["FINGERPRINT"], remote_ed25519_public_key=id_b["PUB_SIGNING"],
            remote_x25519_public_key=id_b["PUB_KX"], trust_status=TrustStatus.TRUSTED,
            transfer_mode="BIDIRECTIONAL", remote_transfer_mode="BIDIRECTIONAL",
        )
        session.add(conn_b)
        path_a = MonitoredPath(name="Hot A", source_path=str(INSTANCE_A_DIR / "hot"))
        path_a.storage_locations.append(cold_a)
        session.add(path_a)
        session.commit()
        path_a_id = path_a.id
        conn_b_id = conn_b.id

    engine_b = create_engine(f"sqlite:///{db_b_path}")
    SessionB = sessionmaker(bind=engine_b)
    with SessionB() as session:
        session.add(InstanceMetadata(instance_uuid=str(uuid.uuid4()), instance_url=f"http://localhost:{PORT_B}", instance_name="Instance B"))
        admin = User(username="admin", password_hash=hash_password("password123"), is_active=True, roles=["admin"])
        session.add(admin)
        cold_b = ColdStorageLocation(name="Cold B", path=str(INSTANCE_B_DIR / "cold"), caution_threshold_percent=20, critical_threshold_percent=10)
        session.add(cold_b)
        conn_a = RemoteConnection(
            name="Instance A", url=f"http://localhost:{PORT_A}",
            remote_fingerprint=id_a["FINGERPRINT"], remote_ed25519_public_key=id_a["PUB_SIGNING"],
            remote_x25519_public_key=id_a["PUB_KX"], trust_status=TrustStatus.TRUSTED,
            transfer_mode="BIDIRECTIONAL", remote_transfer_mode="BIDIRECTIONAL",
        )
        session.add(conn_a)
        path_b = MonitoredPath(name="Hot B", source_path=str(INSTANCE_B_DIR / "hot"))
        path_b.storage_locations.append(cold_b)
        session.add(path_b)
        session.commit()
        path_b_id = path_b.id
        conn_a_id = conn_a.id

    print("\n--- Step 3: Starting Uvicorn Instances (Scheduler Disabled) ---")
    proc_a, log_a = await start_instance("A", PORT_A, INSTANCE_A_DIR)
    proc_b, log_b = await start_instance("B", PORT_B, INSTANCE_B_DIR)
    if not proc_a or not proc_b: return

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            print("Logging in...")
            login_a = await client.post(f"http://localhost:{PORT_A}/api/v1/auth/login", json={"username": "admin", "password": "password123"})
            token_a = parse_api_json(login_a)[0]["access_token"]
            headers_a = {"Authorization": f"Bearer {token_a}"}
            login_b = await client.post(f"http://localhost:{PORT_B}/api/v1/auth/login", json={"username": "admin", "password": "password123"})
            token_b = parse_api_json(login_b)[0]["access_token"]
            headers_b = {"Authorization": f"Bearer {token_b}"}

            # 4. Test PUSH: A -> B
            print("\n--- Step 4: Testing PUSH (A -> B) ---")
            print("Triggering scan on A...")
            await client.post(f"http://localhost:{PORT_A}/api/v1/paths/{path_a_id}/scan", headers=headers_a)
            file_id_a = None
            for _ in range(15):
                resp = await client.get(f"http://localhost:{PORT_A}/api/v1/files?path_id={path_a_id}", headers=headers_a)
                objs = parse_api_json(resp)
                for obj in objs:
                    if obj.get("type") == "file" and "push_test.dat" in obj["data"]["file_path"]:
                        file_id_a = obj["data"]["id"]
                        break
                if file_id_a: break
                await asyncio.sleep(1)

            if not file_id_a:
                print("FAILURE: File not found in A's inventory.")
            else:
                print(f"Triggering push of file {file_id_a} to B...")
                push_resp = await client.post(f"http://localhost:{PORT_A}/api/v1/remote/migrate",
                                             json={"file_inventory_id": file_id_a, "remote_connection_id": conn_b_id, "remote_monitored_path_id": path_b_id},
                                             headers=headers_a)
                job_id = parse_api_json(push_resp)[0]["id"]

                print(f"Manually triggering transfer on A for job {job_id}...")
                trigger_script = TEST_DIR / "run_transfer_a.py"
                with open(trigger_script, "w") as f:
                    f.write(f'import asyncio, os, sys\nsys.path.insert(0, "{BASE_DIR}")\nos.environ["DATABASE_PATH"] = "{db_a_path}"\nos.environ["SECRET_KEY"] = "{SECRET_KEY}"\nfrom app.services.remote_transfer_service import remote_transfer_service\nasync def main(): await remote_transfer_service.run_transfer({job_id})\nif __name__ == "__main__": asyncio.run(main())')
                run_proc = await asyncio.create_subprocess_exec(sys.executable, str(trigger_script), env={**os.environ, "PYTHONPATH": str(BASE_DIR)})
                await run_proc.wait()

                # Verify on B
                expected_file_b = INSTANCE_B_DIR / "hot" / "push_test.dat"
                if expected_file_b.exists():
                    print("SUCCESS: File verified on B.")
                else:
                    print("FAILURE: File not found on B.")

            # 5. Test PULL: A pulls from B
            print("\n--- Step 5: Testing PULL (A pulls from B) ---")
            print("Triggering scan on B...")
            await client.post(f"http://localhost:{PORT_B}/api/v1/paths/{path_b_id}/scan", headers=headers_b)
            file_id_b = None
            for _ in range(15):
                resp = await client.get(f"http://localhost:{PORT_B}/api/v1/files?path_id={path_b_id}", headers=headers_b)
                objs = parse_api_json(resp)
                for obj in objs:
                    if obj.get("type") == "file" and "pull_test.dat" in obj["data"]["file_path"]:
                        file_id_b = obj["data"]["id"]
                        break
                if file_id_b: break
                await asyncio.sleep(1)

            if not file_id_b:
                print("FAILURE: File not found in B's inventory.")
            else:
                print("Triggering pull request from A...")
                pull_resp = await client.post(f"http://localhost:{PORT_A}/api/v1/remote/pull",
                                             json={"remote_file_inventory_id": file_id_b, "remote_connection_id": conn_b_id, "local_monitored_path_id": path_a_id, "strategy": "COPY"},
                                             headers=headers_a)
                print("Pull request accepted.")

                # The remote instance (B) will automatically start pushing chunks to A.
                # We just need to wait for the file to appear on A.
                print("Waiting for file to appear on A (pulling)...")
                expected_file_a = INSTANCE_A_DIR / "hot" / "pull_test.dat"
                success = False
                for _ in range(20):
                    if expected_file_a.exists():
                        success = True
                        break
                    await asyncio.sleep(2)

                if success:
                    print("SUCCESS: File verified on A.")
                else:
                    print("FAILURE: File not found on A.")

    finally:
        print("\n--- Shutting down ---")
        proc_a.terminate()
        proc_b.terminate()
        await asyncio.gather(proc_a.wait(), proc_b.wait())
        log_a.cancel()
        log_b.cancel()

if __name__ == "__main__":
    try:
        asyncio.run(asyncio.wait_for(run_test(), timeout=600))
    except asyncio.TimeoutError:
        print("\nERROR: Remote connection test timed out after 10 minutes.")
        sys.exit(1)
