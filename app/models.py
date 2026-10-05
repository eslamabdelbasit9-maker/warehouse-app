from datetime import datetime, date

from sqlalchemy import (Boolean, Date, DateTime, Float, ForeignKey, Integer, LargeBinary,
                        String, Text, UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base

# ---------- ثوابت ----------
ROLES = {
    "admin": "مدير النظام",
    "storekeeper": "أمين مستودع",
    "transfer": "تحويل بين الفروع",
    "requester": "طالب صرف",
    "engineer": "مهندس معتمد",
    "manager": "مدير الإنتاج",
    "final": "الاعتماد النهائي",
}
UNIT_KINDS = {
    "asphalt": "خلاطة اسفلت",
    "crusher": "كسارة",
    "concrete": "خرسانة",
    "transport": "نقل ومعدات",
    "other": "أخرى",
}
STAGES = {1: "المهندس المعتمد", 2: "مدير الإنتاج", 3: "الاعتماد النهائي"}
STAGE_ROLE = {1: "engineer", 2: "manager", 3: "final"}
REQ_TYPES = {"spare": "صرف قطع غيار", "raw": "صرف مواد خام"}
REQ_STATUS = {
    "pending": "قيد الاعتماد",
    "approved": "معتمد",
    "partial": "معتمد جزئياً",
    "rejected": "مرفوض",
    "cancelled": "ملغي",
}
LINE_STATUS = {"pending": "قيد الاعتماد", "approved": "معتمد", "rejected": "مرفوض"}
ENTITY_TYPES = ["مشروع", "عميل"]
DIESEL_PURPOSES = ["تشغيل", "تسخين"]
CUSTODY_CATEGORIES = ["أصول", "مهمات سلامة"]
TRANSFER_STATUS = {"in_transit": "في الطريق", "received": "تم الاستلام", "cancelled": "ملغي"}


class Site(Base):
    __tablename__ = "sites"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    units: Mapped[list["Unit"]] = relationship(back_populates="site", order_by="Unit.name")


class Unit(Base):
    __tablename__ = "units"
    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id"))
    name: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(20), default="other")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    site: Mapped[Site] = relationship(back_populates="units")
    __table_args__ = (UniqueConstraint("site_id", "name"),)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(150))
    employee_no: Mapped[str | None] = mapped_column(String(50))
    roles: Mapped[str] = mapped_column(String(200), default="requester")  # مفصولة بفاصلة
    password_hash: Mapped[str | None] = mapped_column(String(300))
    all_sites: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    sites: Mapped[list[Site]] = relationship(secondary="user_sites")

    def has(self, *roles):
        mine = set(r.strip() for r in self.roles.split(",") if r.strip())
        return "admin" in mine or bool(mine & set(roles))

    @property
    def role_list(self):
        return [r.strip() for r in self.roles.split(",") if r.strip()]

    def site_ids(self, db):
        if self.all_sites or "admin" in self.role_list:
            return [s.id for s in db.query(Site).all()]
        return [s.id for s in self.sites]


class UserSite(Base):
    __tablename__ = "user_sites"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id"), primary_key=True)


class ApprovalRoute(Base):
    """من يعتمد في كل مرحلة. المرحلة 1 حسب نوع الوحدة، و2 و3 عامة.
    site_id فارغ = كل المواقع."""
    __tablename__ = "approval_routes"
    id: Mapped[int] = mapped_column(primary_key=True)
    stage: Mapped[int] = mapped_column(Integer)
    unit_kind: Mapped[str | None] = mapped_column(String(20))
    site_id: Mapped[int | None] = mapped_column(ForeignKey("sites.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    user: Mapped[User] = relationship()
    site: Mapped[Site | None] = relationship()


class Item(Base):
    __tablename__ = "items"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(250))
    uom: Mapped[str] = mapped_column(String(30), default="قطعة")
    category: Mapped[str] = mapped_column(String(10), default="spare")  # spare | raw
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class OpeningBalance(Base):
    __tablename__ = "opening_balances"
    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id"))
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"))
    location: Mapped[str | None] = mapped_column(String(100))
    qty: Mapped[float] = mapped_column(Float, default=0)
    value: Mapped[float] = mapped_column(Float, default=0)
    item: Mapped[Item] = relationship()
    __table_args__ = (UniqueConstraint("site_id", "item_id"),)


class Receipt(Base):
    __tablename__ = "receipts"
    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id"))
    date: Mapped[date] = mapped_column(Date)
    invoice_no: Mapped[str | None] = mapped_column(String(100))
    supplier: Mapped[str | None] = mapped_column(String(200))
    attachment: Mapped[str | None] = mapped_column(String(300))
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    site: Mapped[Site] = relationship()
    created_by: Mapped[User | None] = relationship()
    lines: Mapped[list["ReceiptLine"]] = relationship(back_populates="receipt", cascade="all, delete-orphan")


class ReceiptLine(Base):
    __tablename__ = "receipt_lines"
    id: Mapped[int] = mapped_column(primary_key=True)
    receipt_id: Mapped[int] = mapped_column(ForeignKey("receipts.id"))
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"))
    qty: Mapped[float] = mapped_column(Float)
    unit_price: Mapped[float] = mapped_column(Float, default=0)
    receipt: Mapped[Receipt] = relationship(back_populates="lines")
    item: Mapped[Item] = relationship()

    @property
    def total(self):
        return (self.qty or 0) * (self.unit_price or 0)


class Request(Base):
    __tablename__ = "requests"
    id: Mapped[int] = mapped_column(primary_key=True)
    req_no: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    type: Mapped[str] = mapped_column(String(10))  # spare | raw
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id"))
    unit_id: Mapped[int | None] = mapped_column(ForeignKey("units.id"))
    requester_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    requester_name: Mapped[str | None] = mapped_column(String(150))  # للبيانات المستوردة
    work_date: Mapped[date] = mapped_column(Date)
    reason: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(15), default="pending")
    current_stage: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime)
    imported: Mapped[bool] = mapped_column(Boolean, default=False)
    site: Mapped[Site] = relationship()
    unit: Mapped[Unit | None] = relationship()
    requester: Mapped[User | None] = relationship()
    lines: Mapped[list["RequestLine"]] = relationship(back_populates="request", cascade="all, delete-orphan",
                                                      order_by="RequestLine.id")
    decisions: Mapped[list["LineDecision"]] = relationship(back_populates="request", cascade="all, delete-orphan",
                                                           order_by="LineDecision.id")

    @property
    def requester_display(self):
        return self.requester.name if self.requester else (self.requester_name or "")

    @property
    def total_value(self):
        return sum(l.value or 0 for l in self.lines if l.status == "approved")


class RequestLine(Base):
    __tablename__ = "request_lines"
    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("requests.id"))
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"))
    qty: Mapped[float] = mapped_column(Float)
    # مواد خام
    entity_type: Mapped[str | None] = mapped_column(String(20))
    entity_name: Mapped[str | None] = mapped_column(String(200))
    diesel_purpose: Mapped[str | None] = mapped_column(String(20))
    # الاعتماد
    status: Mapped[str] = mapped_column(String(10), default="pending")
    rejected_stage: Mapped[int | None] = mapped_column(Integer)
    # التكلفة (تُثبت عند الاعتماد النهائي — قطع الغيار فقط)
    unit_cost: Mapped[float | None] = mapped_column(Float)
    value: Mapped[float | None] = mapped_column(Float)
    request: Mapped[Request] = relationship(back_populates="lines")
    item: Mapped[Item] = relationship()


class LineDecision(Base):
    __tablename__ = "line_decisions"
    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("requests.id"))
    line_id: Mapped[int] = mapped_column(ForeignKey("request_lines.id"))
    stage: Mapped[int] = mapped_column(Integer)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    decision: Mapped[str] = mapped_column(String(10))  # approved | rejected
    comment: Mapped[str | None] = mapped_column(Text)
    at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    request: Mapped[Request] = relationship(back_populates="decisions")
    line: Mapped[RequestLine] = relationship()
    user: Mapped[User] = relationship()


class CustodyRecord(Base):
    __tablename__ = "custody_records"
    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id"))
    employee_no: Mapped[str] = mapped_column(String(50), index=True)
    employee_name: Mapped[str] = mapped_column(String(150))
    category: Mapped[str] = mapped_column(String(30))
    item_name: Mapped[str] = mapped_column(String(250))
    qty: Mapped[float] = mapped_column(Float, default=1)
    serial_no: Mapped[str | None] = mapped_column(String(100))
    issued_at: Mapped[date] = mapped_column(Date)
    issued_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    attachment: Mapped[str | None] = mapped_column(String(300))
    notes: Mapped[str | None] = mapped_column(Text)
    returned_at: Mapped[date | None] = mapped_column(Date)
    return_condition: Mapped[str | None] = mapped_column(String(100))
    return_notes: Mapped[str | None] = mapped_column(Text)
    site: Mapped[Site] = relationship()
    issued_by: Mapped[User | None] = relationship()


class Transfer(Base):
    """تحويل قطع غيار بين موقعين: يُخصم من المرسِل عند الإرسال، ويُضاف للمستلِم عند تأكيد الاستلام
    بنفس تكلفة الإرسال (متوسط سعر المرسِل وقتها)."""
    __tablename__ = "transfers"
    id: Mapped[int] = mapped_column(primary_key=True)
    tr_no: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    from_site_id: Mapped[int] = mapped_column(ForeignKey("sites.id"))
    to_site_id: Mapped[int] = mapped_column(ForeignKey("sites.id"))
    date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(15), default="in_transit")
    note: Mapped[str | None] = mapped_column(Text)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    received_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    received_at: Mapped[datetime | None] = mapped_column(DateTime)
    receive_note: Mapped[str | None] = mapped_column(Text)
    from_site: Mapped[Site] = relationship(foreign_keys=[from_site_id])
    to_site: Mapped[Site] = relationship(foreign_keys=[to_site_id])
    created_by: Mapped[User | None] = relationship(foreign_keys=[created_by_id])
    received_by: Mapped[User | None] = relationship(foreign_keys=[received_by_id])
    lines: Mapped[list["TransferLine"]] = relationship(back_populates="transfer", cascade="all, delete-orphan",
                                                       order_by="TransferLine.id")

    @property
    def total_value(self):
        return sum(l.sent_value for l in self.lines)


class TransferLine(Base):
    __tablename__ = "transfer_lines"
    id: Mapped[int] = mapped_column(primary_key=True)
    transfer_id: Mapped[int] = mapped_column(ForeignKey("transfers.id"))
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"))
    qty: Mapped[float] = mapped_column(Float)                       # المُرسَل
    unit_cost: Mapped[float] = mapped_column(Float, default=0)      # متوسط سعر المرسِل وقت الإرسال
    recv_qty: Mapped[float | None] = mapped_column(Float)           # المُستلَم فعلياً
    transfer: Mapped[Transfer] = relationship(back_populates="lines")
    item: Mapped[Item] = relationship()

    @property
    def sent_value(self):
        return round((self.qty or 0) * (self.unit_cost or 0), 2)


class Attachment(Base):
    """المرفقات تُحفظ داخل قاعدة البيانات (تبقى مع النسخ الاحتياطي ولا تضيع عند إعادة تشغيل الاستضافة)."""
    __tablename__ = "attachments"
    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    filename: Mapped[str] = mapped_column(String(300))
    content_type: Mapped[str] = mapped_column(String(100))
    data: Mapped[bytes] = mapped_column(LargeBinary)
    at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class MailLog(Base):
    __tablename__ = "mail_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    to: Mapped[str] = mapped_column(String(500))
    subject: Mapped[str] = mapped_column(String(300))
    html: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="queued")  # sent | failed | outbox
    error: Mapped[str | None] = mapped_column(Text)
    at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
