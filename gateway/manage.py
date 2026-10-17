"""Local operator commands. Passwords are prompted, never placed on a command line."""

import argparse
import asyncio
import getpass

from sqlalchemy import select

from auth.security import hash_password
from models.db import AsyncSessionLocal, User, engine
from models.schemas import RegisterRequest


async def create_admin(email: str, password: str) -> None:
    payload = RegisterRequest(email=email, password=password)
    try:
        async with AsyncSessionLocal() as session:
            if (
                await session.execute(select(User).where(User.email == payload.email))
            ).scalar_one_or_none():
                raise ValueError("Account exists; no permissions changed")
            session.add(
                User(
                    email=payload.email,
                    hashed_password=hash_password(payload.password),
                    is_admin=True,
                )
            )
            await session.commit()
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["create-admin"])
    parser.add_argument("--email", required=True)
    args = parser.parse_args()
    password = getpass.getpass("New administrator password: ")
    if password != getpass.getpass("Repeat password: "):
        raise SystemExit("Passwords differ")
    try:
        asyncio.run(create_admin(args.email, password))
    except ValueError:
        raise SystemExit("Account exists or email/password is invalid") from None
    print("Administrator created.")


if __name__ == "__main__":
    main()
