"""Seed baseline tenancy: one personal + one company account.

Creates a dev user, both accounts, memberships, and the default system classes
per account. Idempotent — safe to run repeatedly; existing rows are reused, not
duplicated. Run with:

    python -m scripts.seed
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.default_classes import DEFAULT_CLASSES
from app.db.models import Account, AccountMember, Class, User
from app.db.session import SessionLocal

DEV_USER_EMAIL = "abdullahasad70@gmail.com"
DEV_USER_NAME = "Dev User"
PERSONAL_ACCOUNT_NAME = "Personal"
COMPANY_ACCOUNT_NAME = "Acme Inc"


def _get_or_create_user(db: Session, email: str, name: str) -> User:
    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        user = User(email=email, name=name)
        db.add(user)
        db.flush()
    return user


def _get_or_create_account(db: Session, *, type_: str, name: str) -> Account:
    account = db.scalar(
        select(Account).where(Account.type == type_, Account.name == name)
    )
    if account is None:
        account = Account(type=type_, name=name)
        db.add(account)
        db.flush()
    return account


def _ensure_membership(db: Session, account: Account, user: User, role: str) -> None:
    exists = db.scalar(
        select(AccountMember).where(
            AccountMember.account_id == account.id,
            AccountMember.user_id == user.id,
        )
    )
    if exists is None:
        db.add(AccountMember(account_id=account.id, user_id=user.id, role=role))


def _seed_classes(db: Session, account: Account) -> int:
    """Insert any missing system classes for `account`; return count added."""
    existing = set(
        db.scalars(select(Class.slug).where(Class.account_id == account.id)).all()
    )
    added = 0
    for seed in DEFAULT_CLASSES:
        if seed.slug in existing:
            continue
        db.add(
            Class(
                account_id=account.id,
                slug=seed.slug,
                name=seed.name,
                description=seed.description,
                is_system=True,
            )
        )
        added += 1
    return added


def seed() -> None:
    with SessionLocal() as db:
        user = _get_or_create_user(db, DEV_USER_EMAIL, DEV_USER_NAME)
        personal = _get_or_create_account(db, type_="personal", name=PERSONAL_ACCOUNT_NAME)
        company = _get_or_create_account(db, type_="company", name=COMPANY_ACCOUNT_NAME)

        # The dev user owns the personal account and is a member of the company one.
        _ensure_membership(db, personal, user, role="owner")
        _ensure_membership(db, company, user, role="owner")

        added_personal = _seed_classes(db, personal)
        added_company = _seed_classes(db, company)
        db.commit()

        print("Seed complete.")
        print(f"  dev user (bearer token) : {user.id}  <{user.email}>")
        print(f"  personal account        : {personal.id}  +{added_personal} classes")
        print(f"  company account         : {company.id}  +{added_company} classes")


if __name__ == "__main__":
    seed()
