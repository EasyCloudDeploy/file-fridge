
def test_exception_leak():
    try:
        raise ValueError("Sensitive info like /etc/shadow or db creds")
    except Exception as e:
        print(f"Detail: {e!s}")

test_exception_leak()
