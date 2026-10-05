"""بيانات تجريبية للمستخدمين ومسار الاعتماد (للتجربة فقط — في التشغيل الفعلي تُضاف من شاشة الإدارة).
الاستخدام: python -m scripts.seed_demo"""
from app.db import SessionLocal
from app.main import init_db
from app.models import ApprovalRoute, Site, Unit, User

DEMO = [
    ("storekeeper@example.com", "أمين المستودع", "storekeeper,requester,transfer"),
    ("requester@example.com", "طالب صرف", "requester"),
    ("eng.crusher@example.com", "مهندس الكسارة", "engineer"),
    ("eng.asphalt@example.com", "مهندس الاسفلت", "engineer"),
    ("eng.concrete@example.com", "مهندس الخرسانة", "engineer"),
    ("prod.manager@example.com", "مدير الإنتاج", "manager"),
    ("final@example.com", "الاعتماد النهائي", "final"),
]


def run():
    init_db()
    db = SessionLocal()
    sites = db.query(Site).all()
    if not sites:
        db.add(Site(code="NJR", name="نجران"))
        db.commit()
        sites = db.query(Site).all()
    for s in sites:
        have = {u.kind for u in s.units}
        if "asphalt" not in have:
            db.add(Unit(site_id=s.id, name="خلاطة اسفلت", kind="asphalt"))
        if "crusher" not in have:
            db.add(Unit(site_id=s.id, name="كسارة", kind="crusher"))
    users = {}
    for email, name, roles in DEMO:
        u = db.query(User).filter_by(email=email).first() or User(email=email, name=name, roles=roles, all_sites=True)
        db.add(u)
        users[email] = u
    db.flush()
    if not db.query(ApprovalRoute).first():
        db.add_all([
            ApprovalRoute(stage=1, unit_kind="crusher", user_id=users["eng.crusher@example.com"].id),
            ApprovalRoute(stage=1, unit_kind="asphalt", user_id=users["eng.asphalt@example.com"].id),
            ApprovalRoute(stage=1, unit_kind="concrete", user_id=users["eng.concrete@example.com"].id),
            ApprovalRoute(stage=1, unit_kind="other", user_id=users["eng.crusher@example.com"].id),
            ApprovalRoute(stage=2, user_id=users["prod.manager@example.com"].id),
            ApprovalRoute(stage=3, user_id=users["final@example.com"].id),
        ])
    db.commit()
    print("تمت إضافة المستخدمين التجريبيين ومسار الاعتماد")


if __name__ == "__main__":
    run()
