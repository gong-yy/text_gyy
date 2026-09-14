"""T2 附件：展示智眸/ePortal 元数据，并可在 T 内替换后回写。"""
from conftest import eportal_form, headers, intake


def test_t2_replacing_an_attachment_syncs_it_without_submitting_the_form(client):
    created = intake(client, "Acme", {"Customer Name": "Acme"}, task_id="T-attachment-replace")

    uploaded = client.post(
        f"/api/orders/{created['order_id']}/attachments/att1",
        files={"file": ("replacement.pdf", b"%PDF-replacement", "application/pdf")},
        headers=headers("sales1"),
    )

    assert uploaded.status_code == 200, uploaded.text
    attachment = uploaded.json()["attachment"]
    assert attachment["slot_name"] == "合同/报价单/ePO"
    assert attachment["field_name"] == "att1"
    assert attachment["filename"] == "replacement.pdf"
    assert attachment["size"] == len(b"%PDF-replacement")

    form = eportal_form(client, created["form_id"])
    assert any(item.get("filename") == "replacement.pdf" for item in form["attachments"])
    assert form["status"] == "draft"
    assert form["fields"].get("stage") != "3"


def test_t2_page_offers_replace_controls_for_all_attachment_slots(client):
    page = client.get("/t2").text

    assert "data-attachment-file" in page
    assert "replaceAttachment" in page
    assert "/attachments/${slotKey}" in page
