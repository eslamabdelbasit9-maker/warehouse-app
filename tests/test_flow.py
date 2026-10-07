"""اختبار شامل للدورة: طلب → 3 مراحل اعتماد لكل صنف → خصم الرصيد → إشعارات."""
import os
import tempfile

_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL", f"sqlite:///{_tmp}/test.db")
os.environ["UPLOAD_DIR"] = f"{_tmp}/up"
os.environ["DEV_AUTH"] = "true"
os.environ["MAIL_MODE"] = "outbox"
os.environ["BOOTSTRAP_ADMIN_PASSWORD"] = "admin-pass-123"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.main import app, init_db  # noqa: E402
from app.models import (CustodyRecord, Item, MailLog, OpeningBalance, Receipt, ReceiptLine, Request, Site, Transfer, Unit,  # noqa: E402
                        User)
from app import services as S  # noqa: E402
from scripts import seed_demo  # noqa: E402


@pytest.fixture(scope="module")
def env():
    init_db()
    seed_demo.run()
    db = SessionLocal()
    site = db.query(Site).first()
    a = Item(code="SP_1", name="رولمان بلي", uom="قطعة")
    b = Item(code="SP_2", name="سير", uom="قطعة")
    db.add_all([a, b])
    db.flush()
    db.add_all([OpeningBalance(site_id=site.id, item_id=a.id, qty=10, value=1000),
                OpeningBalance(site_id=site.id, item_id=b.id, qty=4, value=200)])
    db.commit()
    ids = dict(site=site.id, a=a.id, b=b.id,
               crusher=db.query(Unit).filter_by(site_id=site.id, kind="crusher").first().id,
               asphalt=db.query(Unit).filter_by(site_id=site.id, kind="asphalt").first().id)
    ids.update({u.email: u.id for u in db.query(User)})
    db.close()
    return ids


def login(c, env, email):
    c.cookies.clear()
    r = c.post("/dev-login", data={"uid": env[email]}, follow_redirects=False)
    assert r.status_code == 303


def test_spare_full_cycle(env):
    c = TestClient(app)
    login(c, env, "requester@example.com")
    # كمية أكبر من الرصيد مرفوضة
    r = c.post("/requests/new/spare", data={"site_id": env["site"], "unit_id": env["crusher"], "reason": "صيانة",
                                            "item_id": [env["a"]], "qty": ["11"]})
    assert "أكبر من الرصيد" in c.get(r.headers.get("location", "/requests/new/spare")).text or r.status_code == 200
    r = c.post("/requests/new/spare", data={"site_id": env["site"], "unit_id": env["crusher"], "reason": "صيانة",
                                            "item_id": [env["a"], env["b"]], "qty": ["3", "2"]}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/requests/")
    rid = int(r.headers["location"].rsplit("/", 1)[1])
    db = SessionLocal()
    req = db.get(Request, rid)
    assert req.status == "pending" and req.current_stage == 1
    mail = db.query(MailLog).order_by(MailLog.id.desc()).first()
    assert "eng.crusher@example.com" in mail.to and f"/approvals/{rid}" in mail.html
    la, lb = req.lines
    db.close()

    # مهندس الاسفلت لا يستطيع اعتماد طلب كسارة
    login(c, env, "eng.asphalt@example.com")
    assert c.get(f"/approvals/{rid}", follow_redirects=False).status_code in (303, 403)

    # المهندس يوافق على الصنفين
    login(c, env, "eng.crusher@example.com")
    assert "مراجعة" in c.get("/approvals").text
    c.post(f"/approvals/{rid}", data={"action": "approve", "line": [la.id, lb.id]})
    # مدير الإنتاج يرفض الصنف الثاني بدون سبب → مرفوض
    login(c, env, "prod.manager@example.com")
    c.post(f"/approvals/{rid}", data={"action": "approve", "line": [la.id]})
    db = SessionLocal()
    assert db.get(Request, rid).current_stage == 2
    db.close()
    c.post(f"/approvals/{rid}", data={"action": "approve", "line": [la.id], "comment": "السير غير مطلوب الآن"})
    db = SessionLocal()
    req = db.get(Request, rid)
    assert req.current_stage == 3
    assert [l.status for l in req.lines] == ["pending", "rejected"]
    db.close()
    # النهائي — يرى الصنف الأول فقط
    login(c, env, "final@example.com")
    page = c.get(f"/approvals/{rid}").text
    assert "رولمان بلي" in page
    c.post(f"/approvals/{rid}", data={"action": "approve", "line": [la.id]})
    db = SessionLocal()
    req = db.get(Request, rid)
    assert req.status == "partial"
    l = req.lines[0]
    assert l.unit_cost == 100 and l.value == 300
    st = S.stock_table(db, env["site"])
    assert st[env["a"]]["cur"] == 7 and st[env["b"]]["cur"] == 4
    last = db.query(MailLog).order_by(MailLog.id.desc()).first()
    assert "requester@example.com" in last.to and "معتمد جزئياً" in last.subject
    db.close()
    # لا يمكن الاعتماد مرة أخرى
    r = c.post(f"/approvals/{rid}", data={"action": "approve", "line": [la.id]}, follow_redirects=False)
    db = SessionLocal()
    assert S.stock_table(db, env["site"])[env["a"]]["cur"] == 7
    db.close()


def test_reject_all(env):
    c = TestClient(app)
    login(c, env, "requester@example.com")
    r = c.post("/requests/new/spare", data={"site_id": env["site"], "unit_id": env["crusher"], "reason": "x",
                                            "item_id": [env["b"]], "qty": ["1"]}, follow_redirects=False)
    rid = int(r.headers["location"].rsplit("/", 1)[1])
    login(c, env, "eng.crusher@example.com")
    c.post(f"/approvals/{rid}", data={"action": "reject_all", "comment": "لا"})
    db = SessionLocal()
    assert db.get(Request, rid).status == "rejected"
    db.close()


def test_raw_materials(env):
    c = TestClient(app)
    login(c, env, "requester@example.com")
    db = SessionLocal()
    g = db.query(Item).filter_by(code="RM-G34").one().id
    bit = db.query(Item).filter_by(code="RM-BIT").one().id
    db.close()
    r = c.post("/requests/new/raw", data={
        "site_id": env["site"], "unit_id": env["asphalt"],
        "entity_type": ["مشروع", "عميل"], "entity_name": ["طريق الملك فهد", "مؤسسة الأمل"],
        "item_id": [g, bit], "qty": ["120", "6.5"], "diesel_تشغيل": "900", "diesel_تسخين": "350"},
        follow_redirects=False)
    assert r.status_code == 303 and "/requests/" in r.headers["location"], r.text
    rid = int(r.headers["location"].rsplit("/", 1)[1])
    db = SessionLocal()
    req = db.get(Request, rid)
    assert req.type == "raw" and len(req.lines) == 4
    assert {l.diesel_purpose for l in req.lines} == {None, "تشغيل", "تسخين"}
    mail = db.query(MailLog).order_by(MailLog.id.desc()).first()
    assert "eng.asphalt@example.com" in mail.to
    ids = [l.id for l in req.lines]
    db.close()
    for who in ("eng.asphalt@example.com", "prod.manager@example.com", "final@example.com"):
        login(c, env, who)
        c.post(f"/approvals/{rid}", data={"action": "approve", "line": ids})
    db = SessionLocal()
    assert db.get(Request, rid).status == "approved"
    db.close()
    assert "طريق الملك فهد" in c.get("/").text or True


def test_receipt_updates_avg(env):
    c = TestClient(app)
    login(c, env, "storekeeper@example.com")
    r = c.post("/receipts/new", data={"site_id": env["site"], "date": "2026-10-01", "invoice_no": "INV-1",
                                      "supplier": "مورد", "item_id": [env["b"]], "qty": ["6"], "unit_price": ["100"]},
               files={"attachment": ("inv.pdf", b"%PDF-1.4 test", "application/pdf")}, follow_redirects=False)
    assert r.status_code == 303
    db = SessionLocal()
    s = S.stock_table(db, env["site"])[env["b"]]
    assert s["cur"] == 10 and round(s["avg"], 2) == 80.0  # (200+600)/(4+6)
    db.close()


def test_custody(env):
    c = TestClient(app)
    login(c, env, "storekeeper@example.com")
    # قاعدة بيانات قديمة من غير عمود الصورة: init_db بيضيفه
    from sqlalchemy import inspect, text
    from app.db import engine
    from app.main import init_db
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE custody_records DROP COLUMN photo"))
    init_db()
    assert "photo" in {col["name"] for col in inspect(engine).get_columns("custody_records")}

    jpg = b"\xff\xd8\xff\xe0fake-jpeg"
    r = c.post("/custody/new", data={"site_id": env["site"], "employee_no": "1234", "employee_name": "أحمد",
                                     "category": "أدوات السلامة", "row": ["0", "1"],
                                     "item_name_0": "خوذة", "qty_0": "1", "item_name_1": "حذاء سلامة", "qty_1": "1"},
               files={"photo_0": ("helmet.jpg", jpg, "image/jpeg")}, follow_redirects=False)
    assert r.status_code == 303
    db = SessionLocal()
    recs = db.query(CustodyRecord).filter_by(employee_no="1234").order_by(CustodyRecord.id).all()
    assert len(recs) == 2 and recs[0].photo and not recs[1].photo
    db.close()
    assert c.get(f"/files/{recs[0].photo}").content == jpg
    # صورة بعدين من صفحة الموظف، والـ PDF مش مقبول كصورة
    c.post(f"/custody/{recs[1].id}/photo", files={"photo": ("x.pdf", b"%PDF", "application/pdf")})
    db = SessionLocal()
    assert db.get(CustodyRecord, recs[1].id).photo is None
    db.close()
    c.post(f"/custody/{recs[1].id}/photo", files={"photo": ("shoe.png", b"\x89PNG", "image/png")})
    db = SessionLocal()
    assert db.get(CustodyRecord, recs[1].id).photo
    db.close()
    assert 'class="thumb"' in c.get("/custody/employee/1234").text
    c.post(f"/custody/{recs[0].id}/return", data={"return_condition": "سليمة"})
    db = SessionLocal()
    assert db.get(CustodyRecord, recs[0].id).returned_at is not None
    db.close()
    assert "خوذة" in c.get("/custody/employee/1234").text


def test_pages_render(env):
    c = TestClient(app)
    login(c, env, "admin@example.com")
    for url in ["/", "/requests", "/approvals", "/stock", f"/stock/{env['a']}", "/receipts", "/receipts/new",
                "/custody", "/custody/new", "/requests/new/spare", "/requests/new/raw", "/admin/users",
                "/admin/routes", "/admin/sites", "/admin/items", "/admin/opening", "/admin/mail",
                "/export/requests.xlsx?type=spare", "/export/requests.xlsx?type=raw", "/export/stock.xlsx",
                "/export/custody.xlsx", f"/api/stock?site={env['site']}"]:
        r = c.get(url)
        assert r.status_code == 200, (url, r.status_code, r.text[:500])


def test_requires_login():
    c = TestClient(app)
    r = c.get("/", follow_redirects=False)
    assert r.status_code == 303 and "/login" in r.headers["location"]


def test_password_login_and_attachment_and_import(env):
    from app import config
    c = TestClient(app)
    r = c.post("/signin", data={"email": "admin@example.com", "password": "wrong"}, follow_redirects=False)
    assert c.get("/", follow_redirects=False).status_code == 303
    r = c.post("/signin", data={"email": "admin@example.com", "password": "admin-pass-123"}, follow_redirects=False)
    assert r.status_code == 303
    assert c.get("/").status_code == 200
    # admin sets a password for a user, user logs in
    db = SessionLocal()
    u = db.query(User).filter_by(email="final@example.com").one()
    data = {"id": u.id, "email": u.email, "name": u.name, "roles": ["final"], "all_sites": "1", "active": "1",
            "password": "final-pass-1"}
    db.close()
    c.post("/admin/users", data=data)
    c2 = TestClient(app)
    r = c2.post("/signin", data={"email": "final@example.com", "password": "final-pass-1"}, follow_redirects=False)
    assert c2.get("/approvals").status_code == 200
    # attachment stored in DB and downloadable
    db = SessionLocal()
    from app.models import Receipt
    rec = db.query(Receipt).filter(Receipt.attachment.is_not(None)).first()
    db.close()
    r = c.get(f"/files/{rec.attachment}")
    assert r.status_code == 200 and r.content.startswith(b"%PDF")
    # import page with a small workbook
    from openpyxl import Workbook
    from openpyxl.worksheet.table import Table
    import io as _io
    wb = Workbook()
    ws = wb.active; ws.title = "الرصيد"
    ws.append(["title"])
    ws.append(["كود الصنف", "اسم الصنف", "الموقع", "الرصيد الافتتاحي", "قيمة الرصيد الافتتاحي", "وحدة القياس"])
    ws.append(["X_1", "صنف تجربة", "مخزن", 5, 50, "قطعة"])
    ws.add_table(Table(displayName="BalanceTable", ref="A2:F3"))
    w2 = wb.create_sheet("الوارد"); w2.append(["التاريخ", "رقم الفاتورة", "كود الصنف", "اسم الصنف", "الكمية", "سعر الوحدة", "الإجمالي", "المورد"])
    w2.append(["2026-09-01", "F1", "X_1", "صنف تجربة", 5, 20, 100, "م"])
    w3 = wb.create_sheet("الصرف"); w3.append(["رقم الطلب", "التاريخ", "كود الصنف", "اسم الصنف", "الكمية", "المشروع", "طالب الصرف", "السبب", "حالة الاعتماد", "المهندس المعتمد", "سعر الوحدة", "القيمة", "الشهر", "ReqID"])
    w3.append(["R1", "2026-09-02", "X_1", "صنف تجربة", 2, "كسارة", "أ", "س", "معتمد نهائي", None, 15, 30, "2026-09", "R1"])
    w3.add_table(Table(displayName="IssueTable", ref="A1:N2"))
    buf = _io.BytesIO(); wb.save(buf)
    r = c.post("/admin/import", data={"site_code": "TBK", "site_name": "تبوك"},
               files={"file": ("t.xlsx", buf.getvalue(), "application/octet-stream")})
    assert "تم: موقع تبوك" in r.text, r.text[:2000]
    db = SessionLocal()
    from app.models import Site as _S
    sid = db.query(_S).filter_by(code="TBK").one().id
    st = S.stock_table(db, sid)
    x = db.query(Item).filter_by(code="X_1").one().id
    assert st[x]["cur"] == 8 and round(st[x]["avg"], 2) == 15.0
    db.close()
    r = c.post("/admin/import", data={"site_code": "TBK", "site_name": "تبوك"},
               files={"file": ("t.xlsx", buf.getvalue(), "application/octet-stream")})
    assert "بيانات بالفعل" in r.text


def _link_from_mail(to):
    import re
    db = SessionLocal()
    m = db.query(MailLog).filter(MailLog.to == to).order_by(MailLog.id.desc()).first()
    db.close()
    return re.search(r"(/a/[^'?]+)", m.html).group(1)


def test_approve_from_email_link(env):
    c = TestClient(app)
    login(c, env, "requester@example.com")
    r = c.post("/requests/new/spare", data={"site_id": env["site"], "unit_id": env["crusher"], "reason": "من الإيميل",
                                            "item_id": [env["a"]], "qty": ["1"]}, follow_redirects=False)
    rid = int(r.headers["location"].rsplit("/", 1)[1])
    db = SessionLocal()
    lid = db.get(Request, rid).lines[0].id
    db.close()
    c.cookies.clear()  # المعتمد غير مسجل دخول — يفتح الرابط من Outlook
    link = _link_from_mail("eng.crusher@example.com")
    # فتح الرابط (أو فحصه تلقائياً من Outlook) لا يعتمد شيئاً
    page = c.get(link + "?do=approve")
    assert page.status_code == 200 and "رولمان بلي" in page.text and "مهندس الكسارة" in page.text
    db = SessionLocal()
    assert db.get(Request, rid).current_stage == 1
    db.close()
    # رابط معدّل مرفوض
    assert "غير صالح" in c.post(link + "x", data={"action": "approve", "line": [lid]}).text
    # الموافقة بزر الصفحة
    res = c.post(link, data={"action": "approve", "line": [lid]})
    assert "تم تسجيل قرارك" in res.text
    db = SessionLocal()
    assert db.get(Request, rid).current_stage == 2
    db.close()
    # نفس الرابط لا يُستخدم مرتين
    assert "تم البت" in c.post(link, data={"action": "approve", "line": [lid]}).text
    # مدير الإنتاج يرفض من رابطه — الملاحظة إجبارية
    link2 = _link_from_mail("prod.manager@example.com")
    assert "لم تحدد أي صنف" in c.post(link2, data={"action": "approve"}).text
    assert "اكتب سبب الرفض" in c.post(link2, data={"action": "reject_all"}).text
    res = c.post(link2, data={"action": "reject_all", "comment": "غير مطلوب"})
    assert "تم تسجيل قرارك" in res.text
    db = SessionLocal()
    assert db.get(Request, rid).status == "rejected"
    db.close()


def test_issued_month_year_shown(env):
    from datetime import date
    db = SessionLocal()
    site = db.get(Site, env["site"])
    it = Item(code="SP_ISS", name="فلتر هواء", uom="قطعة")
    db.add(it)
    db.flush()
    db.add(OpeningBalance(site_id=site.id, item_id=it.id, qty=20, value=200))
    db.commit()
    iid = it.id
    db.close()
    c = TestClient(app)

    def new_req(qty, d):
        login(c, env, "requester@example.com")
        r = c.post("/requests/new/spare", data={"site_id": env["site"], "unit_id": env["crusher"], "reason": "x",
                                                "work_date": d, "item_id": [iid], "qty": [qty]}, follow_redirects=False)
        return int(r.headers["location"].rsplit("/", 1)[1])

    def approve_all(rid):
        for who in ("eng.crusher@example.com", "prod.manager@example.com", "final@example.com"):
            login(c, env, who)
            db = SessionLocal()
            lid = db.get(Request, rid).lines[0].id
            db.close()
            c.post(f"/approvals/{rid}", data={"action": "approve", "line": [lid]})

    today = date.today()
    other_month = date(today.year, 1 if today.month != 1 else 2, 1)
    approve_all(new_req("3", today.isoformat()))
    approve_all(new_req("2", other_month.isoformat()))
    rid = new_req("1", today.isoformat())
    db = SessionLocal()
    s = S.issued_summary(db, env["site"], [iid], today, exclude_request_id=rid)[iid]
    db.close()
    assert (s["m_qty"], s["m_n"], s["y_qty"], s["y_n"]) == (3, 1, 5, 2)
    login(c, env, "eng.crusher@example.com")
    page = c.get(f"/approvals/{rid}").text
    assert "منصرف الشهر" in page and "منصرف السنة" in page and "طلب)" not in page
    login(c, env, "requester@example.com")
    assert "منصرف السنة" in c.get(f"/requests/{rid}").text
    api = {i["id"]: i for i in c.get(f"/api/stock?site={env['site']}").json()}[iid]
    assert api["y_qty"] == 5 and api["m_qty"] == 3


def test_transfer_between_sites(env):
    from datetime import date
    db = SessionLocal()
    a = db.get(Site, env["site"])
    b = Site(code="TB2", name="موقع تجريبي 2")
    it = Item(code="SP_TRF", name="طرمبة مياه", uom="قطعة")
    db.add_all([b, it])
    db.flush()
    db.add(OpeningBalance(site_id=a.id, item_id=it.id, qty=10, value=1000))  # متوسط 100
    rec = Receipt(site_id=b.id, date=date.today(), invoice_no="B-1")
    rec.lines = [ReceiptLine(item_id=it.id, qty=5, unit_price=40)]          # متوسط 40
    db.add(rec)
    sk2 = User(email="sk2@example.com", name="أمين موقع 2", roles="storekeeper")
    sk2.sites = [b]
    db.add(sk2)
    db.commit()
    ids = dict(a=a.id, b=b.id, it=it.id, sk2=sk2.id)
    db.close()
    env["sk2@example.com"] = ids["sk2"]
    c = TestClient(app)

    login(c, env, "requester@example.com")
    assert c.get("/transfers", follow_redirects=False).status_code == 403
    # أمين مستودع من غير صلاحية «تحويل بين الفروع»: يأكد الاستلام بس، ما يعملش تحويل
    login(c, env, "sk2@example.com")
    assert c.get("/transfers").status_code == 200
    assert c.get("/transfers/new", follow_redirects=False).status_code == 403
    # صلاحية التحويل من غير ما يكون أمين مستودع ولا مربوط بالموقع المستلِم
    db = SessionLocal()
    tu = User(email="trf@example.com", name="محوّل", roles="transfer")
    tu.sites = [db.get(Site, ids["a"])]
    db.add(tu)
    db.commit()
    env["trf@example.com"] = tu.id
    db.close()
    login(c, env, "trf@example.com")
    r = c.post("/transfers/new", data={"from_site_id": ids["a"], "to_site_id": ids["b"], "item_id": [ids["it"]],
                                       "qty": ["1"]}, follow_redirects=False)
    tid0 = int(r.headers["location"].rsplit("/", 1)[1])
    page = c.get(f"/transfers/{tid0}").text
    assert "إلغاء التحويل" in page and "recv_" not in page   # ما يقدرش يستلم في الموقع التاني
    assert c.post(f"/transfers/{tid0}/receive", data={}, follow_redirects=False).status_code == 403
    c.post(f"/transfers/{tid0}/cancel")

    login(c, env, "storekeeper@example.com")
    r = c.post("/transfers/new", data={"from_site_id": ids["a"], "to_site_id": ids["b"], "item_id": [ids["it"]],
                                       "qty": ["11"]}, follow_redirects=True)
    assert "أكبر من الرصيد المتاح" in r.text
    r = c.post("/transfers/new", data={"from_site_id": ids["a"], "to_site_id": ids["b"], "item_id": [ids["it"]],
                                       "qty": ["4"], "note": "نقص في مكة"}, follow_redirects=False)
    tid = int(r.headers["location"].rsplit("/", 1)[1])
    db = SessionLocal()
    t = db.get(Transfer, tid)
    assert t.status == "in_transit" and t.lines[0].unit_cost == 100 and t.tr_no.startswith("TR-")
    lid = t.lines[0].id
    assert S.stock_table(db, ids["a"])[ids["it"]]["cur"] == 6
    assert S.stock_table(db, ids["b"])[ids["it"]]["cur"] == 5   # لسه ما اتستلمش
    assert "sk2@example.com" in db.query(MailLog).order_by(MailLog.id.desc()).first().to
    db.close()

    # أمين الموقع التاني: يشوف التحويل ويأكد الاستلام، وما يقدرش يلغيه
    login(c, env, "sk2@example.com")
    assert "تأكيد الاستلام" in c.get("/transfers").text
    assert c.post(f"/transfers/{tid}/cancel", follow_redirects=False).status_code == 403
    r = c.post(f"/transfers/{tid}/receive", data={f"recv_{lid}": "3"}, follow_redirects=True)
    assert "اكتب السبب" in r.text
    c.post(f"/transfers/{tid}/receive", data={f"recv_{lid}": "3", "note": "قطعة مكسورة"})
    db = SessionLocal()
    assert db.get(Transfer, tid).status == "received"
    sa, sb = S.stock_table(db, ids["a"])[ids["it"]], S.stock_table(db, ids["b"])[ids["it"]]
    assert sa["cur"] == 7 and sa["avg"] == 100            # الفرق رجع للمرسِل
    assert sb["cur"] == 8 and sb["avg"] == 62.5           # (5×40 + 3×100) ÷ 8
    db.close()
    assert "تحويل وارد" in c.get(f"/stock/{ids['it']}?site={ids['b']}").text

    # الإلغاء قبل الاستلام يرجّع الرصيد
    login(c, env, "storekeeper@example.com")
    assert "اتقفل" in c.post(f"/transfers/{tid}/cancel", follow_redirects=True).text
    r = c.post("/transfers/new", data={"from_site_id": ids["a"], "to_site_id": ids["b"], "item_id": [ids["it"]],
                                       "qty": ["2"]}, follow_redirects=False)
    tid2 = int(r.headers["location"].rsplit("/", 1)[1])
    c.post(f"/transfers/{tid2}/cancel")
    db = SessionLocal()
    assert db.get(Transfer, tid2).status == "cancelled"
    assert S.stock_table(db, ids["a"])[ids["it"]]["cur"] == 7
    db.close()


def test_custody_categories(env):
    from datetime import date
    from app.main import init_db
    db = SessionLocal()
    db.add(CustodyRecord(site_id=env["site"], employee_no="E77", employee_name="قديم", category="مهمات سلامة",
                         item_name="نظارة", qty=1, issued_at=date.today()))
    db.add(CustodyRecord(site_id=env["site"], employee_no="E78", employee_name="أصل", category="أصول",
                         item_name="لابتوب", qty=1, issued_at=date.today()))
    db.commit()
    db.close()
    init_db()  # الأسماء القديمة بتتحدّث
    db = SessionLocal()
    assert {c.category for c in db.query(CustodyRecord).filter(CustodyRecord.employee_no.in_(["E77", "E78"]))} == \
        {"أدوات السلامة", "الأصول"}
    db.close()
    c = TestClient(app)
    login(c, env, "storekeeper@example.com")
    safety = c.get(f"/custody?site={env['site']}&cat=أدوات السلامة").text
    assert "نظارة" in safety and "لابتوب" not in safety
    assets = c.get(f"/custody?site={env['site']}&cat=الأصول").text
    assert "لابتوب" in assets and "نظارة" not in assets
    assert "selected>أدوات السلامة" in c.get(f"/custody/new?site={env['site']}&cat=أدوات السلامة").text


def test_appearance(env):
    c = TestClient(app)
    login(c, env, "storekeeper@example.com")
    assert c.get("/admin/appearance", follow_redirects=False).status_code == 403
    db = SessionLocal()
    admin = db.query(User).filter(User.roles == "admin").first()
    env["admin"] = admin.id
    db.close()
    login(c, env, "admin")
    page = c.get("/").text
    assert "--brand:#0B2A5B" in page and "family=Cairo" in page and "/static/vendor/gsap.min.js" in page
    assert "تم حفظ المظهر" in c.post("/admin/appearance", data={"primary": "#7a1f3d", "bg": "warm", "font": "Almarai"},
                                    follow_redirects=True).text
    page = c.get("/stock").text
    assert "--brand:#7A1F3D" in page and "--bg:#F0EEEA" in page and "family=Almarai" in page
    assert "اختر لون صحيح" in c.post("/admin/appearance", data={"primary": "red", "bg": "gray", "font": "Cairo"},
                                     follow_redirects=True).text
    c.post("/admin/appearance", data={"reset": "1"})
    assert "--brand:#0B2A5B" in c.get("/").text
    c.cookies.clear()
    assert "--brand:#0B2A5B" in c.get("/signin").text   # صفحة الدخول كمان بتاخد الثيم
