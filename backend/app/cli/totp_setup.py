"""Interactive setup output only; never call from a logged service."""
import base64
import secrets


def main():
    secret = base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")
    print(secret)
    print(f"otpauth://totp/taoli1:admin?secret={secret}&issuer=taoli1")


if __name__ == "__main__":
    main()
