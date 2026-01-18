import argparse
import logging
import os
import sys

# Add the project root to the Python path
project_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, project_root)


from app.database import SessionLocal
from app.models import User
from app.security import hash_password

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def reset_password(username: str, new_password: str):
    """
    Reset a user's password.

    Args:
        username: The username of the user to update.
        new_password: The new password to set.
    """
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == username).first()
        if not user:
            logger.error(f"User '{username}' not found.")
            return

        user.password_hash = hash_password(new_password)
        db.commit()
        logger.info(f"Password for user '{username}' has been reset successfully.")
    finally:
        db.close()


def create_user(username: str, password: str, is_admin: bool):
    """Create a new user."""
    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.username == username).first()
        if existing:
            logger.error(f"User '{username}' already exists.")
            return

        new_user = User(
            username=username, password_hash=hash_password(password), is_admin=is_admin
        )
        db.add(new_user)
        db.commit()
        logger.info(f"User '{username}' created successfully.")
    finally:
        db.close()


def main():
    """Main function to parse arguments and call the appropriate function."""
    parser = argparse.ArgumentParser(description="User management script for File Fridge.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Sub-command for resetting a password
    reset_parser = subparsers.add_parser("reset-password", help="Reset a user's password.")
    reset_parser.add_argument("username", help="The username of the user.")
    reset_parser.add_argument("new_password", help="The new password for the user.")

    # Sub-command for creating a user
    create_parser = subparsers.add_parser("create-user", help="Create a new user.")
    create_parser.add_argument("username", help="The username of the user.")
    create_parser.add_argument("password", help="The password for the user.")
    create_parser.add_argument("--admin", action="store_true", help="Set as admin.")

    args = parser.parse_args()

    if args.command == "reset-password":
        reset_password(args.username, args.new_password)
    elif args.command == "create-user":
        create_user(args.username, args.password, args.admin)


if __name__ == "__main__":
    main()
