"""Class-catalog endpoints: list (with counts), create custom, delete, isolation."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.db.models import Class, Document, DocumentClass
from app.db.session import SessionLocal
from app.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _headers(seeded_account, *, account="personal_id") -> dict:
    return {
        "Authorization": f"Bearer {seeded_account['user_id']}",
        "X-Account-Id": str(seeded_account[account]),
    }


def test_list_classes_empty_and_auth(client, seeded_account) -> None:
    assert client.get("/api/v1/classes").status_code == 401
    res = client.get("/api/v1/classes", headers=_headers(seeded_account))
    assert res.status_code == 200
    assert res.json() == []  # seeded_account seeds no classes


def test_create_class_derives_slug(client, seeded_account) -> None:
    res = client.post(
        "/api/v1/classes", headers=_headers(seeded_account),
        json={"name": "Purchase Order", "description": "POs issued to suppliers"},
    )
    assert res.status_code == 201
    body = res.json()
    assert body["slug"] == "purchase_order"
    assert body["is_system"] is False
    assert body["document_count"] == 0
    # and it now shows up in the list
    listed = client.get("/api/v1/classes", headers=_headers(seeded_account)).json()
    assert [c["slug"] for c in listed] == ["purchase_order"]


def test_create_duplicate_slug_conflict(client, seeded_account) -> None:
    h = _headers(seeded_account)
    client.post("/api/v1/classes", headers=h, json={"name": "Meeting Notes"})
    res = client.post("/api/v1/classes", headers=h, json={"name": "meeting  notes"})  # same slug
    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "class_exists"


def test_create_invalid_name_400(client, seeded_account) -> None:
    res = client.post("/api/v1/classes", headers=_headers(seeded_account), json={"name": "!!!"})
    assert res.status_code == 400
    assert res.json()["detail"]["code"] == "invalid_name"


def test_delete_custom_class(client, seeded_account) -> None:
    h = _headers(seeded_account)
    cid = client.post("/api/v1/classes", headers=h, json={"name": "Temp"}).json()["id"]
    assert client.delete(f"/api/v1/classes/{cid}", headers=h).status_code == 204
    assert client.get("/api/v1/classes", headers=h).json() == []


def test_delete_system_class_immutable(client, seeded_account) -> None:
    with SessionLocal() as db:
        sys_cls = Class(
            account_id=seeded_account["personal_id"], slug="invoice",
            name="Invoice", is_system=True,
        )
        db.add(sys_cls)
        db.commit()
        cid = sys_cls.id
    res = client.delete(f"/api/v1/classes/{cid}", headers=_headers(seeded_account))
    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "system_immutable"


def test_delete_unknown_class_404(client, seeded_account) -> None:
    res = client.delete(f"/api/v1/classes/{uuid.uuid4()}", headers=_headers(seeded_account))
    assert res.status_code == 404


def test_class_account_isolation(client, seeded_account) -> None:
    # Created under personal...
    cid = client.post(
        "/api/v1/classes", headers=_headers(seeded_account), json={"name": "Secret"}
    ).json()["id"]
    # ...invisible and undeletable from the company account.
    assert client.get("/api/v1/classes", headers=_headers(seeded_account, account="company_id")).json() == []
    res = client.delete(
        f"/api/v1/classes/{cid}", headers=_headers(seeded_account, account="company_id")
    )
    assert res.status_code == 404


def test_list_reports_document_count(client, seeded_account) -> None:
    acct = seeded_account["personal_id"]
    with SessionLocal() as db:
        cls = Class(account_id=acct, slug="report", name="Report", is_system=False)
        doc = Document(
            account_id=acct, source="web_upload", original_filename="r.pdf",
            file_hash=uuid.uuid4().hex, storage_path="/tmp/r.pdf", status="indexed",
        )
        db.add_all([cls, doc])
        db.flush()
        db.add(DocumentClass(account_id=acct, document_id=doc.id, class_id=cls.id, confidence=0.9))
        db.commit()
    listed = client.get("/api/v1/classes", headers=_headers(seeded_account)).json()
    report = next(c for c in listed if c["slug"] == "report")
    assert report["document_count"] == 1


def test_documents_filter_by_class(client, seeded_account) -> None:
    acct = seeded_account["personal_id"]
    with SessionLocal() as db:
        inv = Class(account_id=acct, slug="invoice", name="Invoice", is_system=True)
        rpt = Class(account_id=acct, slug="report", name="Report", is_system=True)
        d_inv = Document(account_id=acct, source="web_upload", original_filename="i.pdf",
                         file_hash=uuid.uuid4().hex, storage_path="/tmp/i.pdf", status="indexed")
        d_rpt = Document(account_id=acct, source="web_upload", original_filename="r.pdf",
                         file_hash=uuid.uuid4().hex, storage_path="/tmp/r.pdf", status="indexed")
        db.add_all([inv, rpt, d_inv, d_rpt])
        db.flush()
        db.add_all([
            DocumentClass(account_id=acct, document_id=d_inv.id, class_id=inv.id, confidence=0.9),
            DocumentClass(account_id=acct, document_id=d_rpt.id, class_id=rpt.id, confidence=0.9),
        ])
        db.commit()
        inv_id = str(d_inv.id)

    res = client.get("/api/v1/documents?class=invoice", headers=_headers(seeded_account))
    assert res.status_code == 200
    items = res.json()["items"]
    assert [d["id"] for d in items] == [inv_id]  # only the invoice doc, not the report
