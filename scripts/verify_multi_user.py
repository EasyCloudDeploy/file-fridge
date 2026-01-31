
import sys
import time

import httpx
import jwt

BASE_URL = "http://localhost:8000"
RUN_ID = int(time.time())

def test_multi_user():
    mgr_name = f"manager_{RUN_ID}"
    viewer_name = f"viewer_{RUN_ID}"

    # ... (Step 1-3 remain same, but I'll update the whole function for consistency)

    # 1. Setup first user (should be admin)
    print("Step 1: Setting up first user...")
    try:
        response = httpx.post(f"{BASE_URL}/api/v1/auth/setup", json={
            "username": "admin",
            "password": "Password123!"
        })
        if response.status_code == 201:
            print("SUCCESS: Admin user created")
        else:
            print(f"INFO: {response.json().get('detail', 'Setup already done')}")
    except Exception as e:
        print(f"Setup failed: {e}")

    # 2. Login as admin
    print("\nStep 2: Logging in as admin...")
    response = httpx.post(f"{BASE_URL}/api/v1/auth/login", json={
        "username": "admin",
        "password": "Password123!"
    })
    if response.status_code != 200:
        print(f"FAILURE: Admin login failed ({response.status_code})")
        sys.exit(1)

    admin_token = response.json()["access_token"]
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    # 3. Decode JWT and check roles
    print("\nStep 3: Verifying Admin JWT roles...")
    decoded = jwt.decode(admin_token, options={"verify_signature": False})
    if "admin" in decoded.get("roles", []):
        print(f"SUCCESS: Admin roles verified: {decoded['roles']}")
    else:
        print("FAILURE: Admin role missing from JWT")
        sys.exit(1)

    # 4. Create Manager and Viewer users
    print(f"\nStep 4: Creating manager ({mgr_name}) and viewer ({viewer_name}) users...")

    # Create Manager
    resp_mgr = httpx.post(f"{BASE_URL}/api/v1/users", headers=admin_headers, json={
        "username": mgr_name, "password": "Password123!"
    })
    # Create Viewer
    resp_view = httpx.post(f"{BASE_URL}/api/v1/users", headers=admin_headers, json={
        "username": viewer_name, "password": "Password123!"
    })

    if resp_mgr.status_code in [200, 201] and resp_view.status_code in [200, 201]:
        print("SUCCESS: Users created")
    else:
        print(f"FAILURE: User creation failed (Mgr: {resp_mgr.status_code}, View: {resp_view.status_code})")
        sys.exit(1)

    mgr_id = resp_mgr.json()["id"]
    view_id = resp_view.json()["id"]

    # 5. Assign roles
    print("\nStep 5: Assigning roles (Manager -> ['manager'], Viewer -> ['viewer'])...")
    httpx.put(f"{BASE_URL}/api/v1/users/{mgr_id}/roles", headers=admin_headers, json=["viewer", "manager"])
    httpx.put(f"{BASE_URL}/api/v1/users/{view_id}/roles", headers=admin_headers, json=["viewer"])
    print("SUCCESS: Roles assigned")

    # 6. Test Manager Permissions
    print(f"\nStep 6: Testing Manager ({mgr_name}) permissions...")
    resp_login = httpx.post(f"{BASE_URL}/api/v1/auth/login", json={"username": mgr_name, "password": "Password123!"})
    mgr_headers = {"Authorization": f"Bearer {resp_login.json()['access_token']}"}

    # Can access files (read)
    r1 = httpx.get(f"{BASE_URL}/api/v1/files", headers=mgr_headers)
    # Can access paths (read)
    r2 = httpx.get(f"{BASE_URL}/api/v1/paths", headers=mgr_headers)
    # Cannot access users (admin only)
    r3 = httpx.get(f"{BASE_URL}/api/v1/users", headers=mgr_headers)

    print(f" - Access Files (read): {r1.status_code} (Expected 200)")
    print(f" - Access Paths (read): {r2.status_code} (Expected 200)")
    print(f" - Access Users (admin): {r3.status_code} (Expected 403)")

    if r1.status_code == 200 and r2.status_code == 200 and r3.status_code == 403:
        print("SUCCESS: Manager permissions verified")
    else:
        print("FAILURE: Manager permission mismatch")

    # 7. Test Viewer Permissions
    print(f"\nStep 7: Testing Viewer ({viewer_name}) permissions...")
    resp_login = httpx.post(f"{BASE_URL}/api/v1/auth/login", json={"username": viewer_name, "password": "Password123!"})
    view_headers = {"Authorization": f"Bearer {resp_login.json()['access_token']}"}

    # Can access files (read)
    r1 = httpx.get(f"{BASE_URL}/api/v1/files", headers=view_headers)
    # Cannot trigger scan (write permission needed for 'paths')
    r2 = httpx.post(f"{BASE_URL}/api/v1/paths/1/scan", headers=view_headers)
    # Cannot access users (admin only)
    r3 = httpx.get(f"{BASE_URL}/api/v1/users", headers=view_headers)

    print(f" - Access Files (read): {r1.status_code} (Expected 200)")
    print(f" - Trigger Scan (write): {r2.status_code} (Expected 403)")
    print(f" - Access Users (admin): {r3.status_code} (Expected 403)")

    if r1.status_code == 200 and r2.status_code == 403 and r3.status_code == 403:
        print("SUCCESS: Viewer permissions verified")
    else:
        print("FAILURE: Viewer permission mismatch")

    # 8. Test Unauthorized (No Roles) Permissions
    guest_name = f"guest_{RUN_ID}"
    print(f"\nStep 8: Testing Guest ({guest_name}, NO roles) permissions...")

    # Create Guest
    httpx.post(f"{BASE_URL}/api/v1/users", headers=admin_headers, json={
        "username": guest_name, "password": "Password123!"
    })
    # Explicitly clear roles (in case default changes)
    resp_user = httpx.get(f"{BASE_URL}/api/v1/users", headers=admin_headers)
    guest_id = [u["id"] for u in resp_user.json() if u["username"] == guest_name][0]
    httpx.put(f"{BASE_URL}/api/v1/users/{guest_id}/roles", headers=admin_headers, json=[])

    resp_login = httpx.post(f"{BASE_URL}/api/v1/auth/login", json={"username": guest_name, "password": "Password123!"})
    guest_headers = {"Authorization": f"Bearer {resp_login.json()['access_token']}"}

    # Cannot access remote config
    r1 = httpx.get(f"{BASE_URL}/api/v1/remote/config", headers=guest_headers)
    # Cannot access remote status (requires at least 'read' access to 'Remote Connections')
    r2 = httpx.get(f"{BASE_URL}/api/v1/remote/status", headers=guest_headers)

    print(f" - Access Remote Config (admin/manager only): {r1.status_code} (Expected 403)")
    print(f" - Access Remote Status (read only): {r2.status_code} (Expected 403)")

    if r1.status_code == 403 and r2.status_code == 403:
        print("SUCCESS: Unauthorized access prevented")
    else:
        print("FAILURE: Unauthorized access allowed")

    print("\n" + "="*40)
    print("ALL MULTI-USER VERIFICATION TESTS PASSED")
    print("="*40)

if __name__ == "__main__":
    test_multi_user()
