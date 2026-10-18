"""الحسابات العامة: شجرة الحسابات، القيود التلقائية من مستندات البرنامج، الأرصدة والقوائم، قفل الفترات، سجل النشاط.

القيود التلقائية بتتبني من المستندات نفسها (الفواتير، الصرف المعتمد، الإنتاج، التحويلات، المستخلصات، الأرصدة المنقولة)
وبتتحدّث لوحدها لما المستند يتعدّل (مزامنة بالبصمة) — فمفيش قيد بيتكرر ولا قيد بيفضل بعد حذف مستنده."""
import hashlib
import json
from collections import defaultdict
from datetime import date, datetime

from sqlalchemy import event, func
from sqlalchemy.orm import Session, selectinload

from .models import (Account, AppSetting, AuditLog, CrusherProduction, CrusherProductionLine, Item, JournalEntry,
                     JournalLine, LineDecision, OpeningBalance, Receipt, ReceiptLine, Request, RequestLine, SalesLine,
                     Transfer, TransferLine, Unit)

ACC_VIEW = ("accountant", "manager", "final")   # + مدير النظام
ACC_EDIT = ("accountant",)                      # + مدير النظام


from .services import BusinessError  # noqa: E402


class AccountingError(BusinessError):
    """أخطاء الحسابات وقفل الفترات — نوع من BusinessError فبتتعرض كرسالة في كل الشاشات."""


# ---------------- شجرة الحسابات القياسية ----------------
# (الكود، الاسم، النوع، كود الأب، مجموعة؟، المفتاح اللي القيود التلقائية بتستخدمه)
CHART = [
    ("1", "الأصول", "asset", None, True, None),
    ("11", "الأصول المتداولة", "asset", "1", True, None),
    ("1101", "النقدية والبنوك", "asset", "11", True, None),
    ("110101", "الصندوق", "asset", "1101", False, "cash"),
    ("110102", "البنك", "asset", "1101", False, "bank"),
    ("1102", "العملاء", "asset", "11", False, "ar"),
    ("1103", "المخزون", "asset", "11", True, None),
    ("110301", "مخزون قطع الغيار", "asset", "1103", False, "inv_spare"),
    ("110302", "مخزون المواد الخام", "asset", "1103", False, "inv_raw"),
    ("110303", "مخزون الأصول والعهد", "asset", "1103", False, "inv_asset"),
    ("110304", "مخزون أدوات السلامة", "asset", "1103", False, "inv_safety"),
    ("1104", "ضريبة القيمة المضافة - المدخلات", "asset", "11", False, "vat_in"),
    ("1105", "تحويلات بين الفروع (بضاعة في الطريق)", "asset", "11", False, "transit"),
    ("1106", "سلف وعهد الموظفين", "asset", "11", False, "emp_adv"),
    ("12", "الأصول الثابتة", "asset", "1", True, None),
    ("1201", "المعدات والآلات", "asset", "12", False, None),
    ("1202", "السيارات والشاحنات", "asset", "12", False, None),
    ("2", "الخصوم", "liability", None, True, None),
    ("21", "الخصوم المتداولة", "liability", "2", True, None),
    ("2101", "الموردين", "liability", "21", False, "ap"),
    ("2102", "ضريبة القيمة المضافة - المخرجات", "liability", "21", False, "vat_out"),
    ("2103", "جاري الشركة المنقول منها الأرصدة", "liability", "21", False, "transferred_co"),
    ("2104", "رواتب مستحقة", "liability", "21", False, "salaries_payable"),
    ("3", "حقوق الملكية", "equity", None, True, None),
    ("31", "رأس المال", "equity", "3", False, "capital"),
    ("32", "الأرباح المحتجزة", "equity", "3", False, "retained"),
    ("4", "الإيرادات", "revenue", None, True, None),
    ("41", "إيرادات المبيعات", "revenue", "4", True, None),
    ("4101", "مبيعات الكسارات", "revenue", "41", False, "rev_crusher"),
    ("4102", "مبيعات الأسفلت", "revenue", "41", False, "rev_asphalt"),
    ("4103", "مبيعات الخرسانة", "revenue", "41", False, "rev_concrete"),
    ("4104", "مبيعات أخرى", "revenue", "41", False, "rev_other"),
    ("5", "المصروفات والتكاليف", "expense", None, True, None),
    ("51", "قطع الغيار المصروفة", "expense", "5", True, None),
    ("5101", "قطع غيار الكسارات", "expense", "51", False, "exp_spare_crusher"),
    ("5102", "قطع غيار خلاطات الأسفلت", "expense", "51", False, "exp_spare_asphalt"),
    ("5103", "قطع غيار الخرسانة", "expense", "51", False, "exp_spare_concrete"),
    ("5104", "قطع غيار النقل والمعدات", "expense", "51", False, "exp_spare_transport"),
    ("5105", "قطع غيار أخرى", "expense", "51", False, "exp_spare_other"),
    ("52", "تكلفة المواد الخام المصروفة", "expense", "5", True, None),
    ("5201", "مواد خلاطات الأسفلت", "expense", "52", False, "raw_asphalt"),
    ("5202", "مواد محطات الخرسانة", "expense", "52", False, "raw_concrete"),
    ("5203", "مواد وتشغيل الكسارات", "expense", "52", False, "raw_crusher"),
    ("5204", "تكلفة مواد مباعة", "expense", "52", False, "raw_sale"),
    ("5205", "مواد مشاريع الشركة", "expense", "52", False, "raw_project"),
    ("5206", "مواد أخرى", "expense", "52", False, "raw_other"),
    ("53", "الإنتاج المحمّل على المخزون", "expense", "5", True, None),
    ("5301", "إنتاج الكسارات المحمّل على المخزون", "expense", "53", False, "prod_crusher"),
    ("54", "مصروفات أخرى", "expense", "5", True, None),
    ("5401", "عجز وفروقات المخزون", "expense", "54", False, "inv_diff"),
    ("5402", "الرواتب والأجور", "expense", "54", False, "salaries"),
    ("5403", "مصروفات إدارية وعمومية", "expense", "54", False, None),
]
NORMAL_DEBIT = {"asset", "expense"}   # الأصول والمصروفات رصيدها الطبيعي مدين


def seed_chart(db: Session):
    """يضيف الحسابات القياسية الناقصة بس (ما بيغيّرش أي حساب اتعدّل)."""
    by_code = {a.code: a for a in db.query(Account)}
    keys = {a.system_key for a in by_code.values() if a.system_key}
    for code, name, typ, parent, group, key in CHART:
        if code in by_code or (key and key in keys):
            continue
        a = Account(code=code, name=name, type=typ, is_group=group, system_key=key,
                    parent_id=by_code[parent].id if parent and parent in by_code else None)
        db.add(a)
        db.flush()
        by_code[code] = a
    db.commit()


def acc_by_key(db):
    return {a.system_key: a.id for a in db.query(Account).filter(Account.system_key.isnot(None))}


# ---------------- الإعدادات: بداية القيود وتاريخ القفل ----------------
def _setting(db, key):
    row = db.get(AppSetting, key)
    return row.value if row else None


def _set(db, key, value):
    row = db.get(AppSetting, key)
    if row:
        row.value = value
    else:
        db.add(AppSetting(key=key, value=value))


def start_date(db):
    """القيود التلقائية من أول السنة الحالية (أو التاريخ المحفوظ)."""
    v = _setting(db, "acct_start")
    try:
        return date.fromisoformat(v) if v else date(date.today().year, 1, 1)
    except ValueError:
        return date(date.today().year, 1, 1)


def lock_date(db):
    v = _setting(db, "lock_date")
    try:
        return date.fromisoformat(v) if v else None
    except ValueError:
        return None


def set_lock_date(db, d):
    _set(db, "lock_date", d.isoformat() if d else "")


def check_lock(db, d, what="العملية"):
    """يمنع أي تسجيل أو تعديل أو حذف بتاريخ داخل فترة مقفولة."""
    lk = lock_date(db)
    if lk and d and d <= lk:
        raise AccountingError(f"الفترة حتى {lk} مقفولة — لا يمكن {what} بتاريخ {d}")


# ---------------- سجل النشاط ----------------
def audit(db, user, action, entity=None, entity_id=None, details=None, request=None):
    ip = request.client.host if request is not None and request.client else None
    db.add(AuditLog(user_id=getattr(user, "id", None), user_name=getattr(user, "name", None), action=action,
                    entity=entity, entity_id=str(entity_id) if entity_id is not None else None,
                    details=json.dumps(details, ensure_ascii=False, default=str) if isinstance(details, (dict, list)) else details,
                    ip=ip))


AUDIT_ACTIONS = {"login": "تسجيل دخول", "login_failed": "محاولة دخول فاشلة", "edit": "تعديل", "delete": "حذف",
                 "create": "إضافة", "restore": "استرجاع نسخة احتياطية", "backup": "تحميل نسخة احتياطية",
                 "import": "استيراد", "lock": "قفل الفترات", "users": "المستخدمين"}


# ---------------- المزامنة التلقائية ----------------
_STATE = {"dirty": True}
_WATCH = (Receipt, ReceiptLine, Request, RequestLine, LineDecision, OpeningBalance, CrusherProduction,
          CrusherProductionLine, Transfer, TransferLine, SalesLine, Unit, Item)


@event.listens_for(Session, "after_flush")
def _mark_dirty(session, _ctx):
    for obj in list(session.new) + list(session.dirty) + list(session.deleted):
        if isinstance(obj, _WATCH):
            _STATE["dirty"] = True
            return


def mark_dirty():
    _STATE["dirty"] = True


RAW_DEST_ACC = {"asphalt": "raw_asphalt", "concrete": "raw_concrete", "crusher": "raw_crusher", "sale": "raw_sale",
                "project": "raw_project", "transfer": "transit"}


def _inv(cat):
    return {"raw": "inv_raw", "asset": "inv_asset", "safety": "inv_safety"}.get(cat, "inv_spare")


class _E:
    """قيد تحت التجهيز: سطور (مفتاح الحساب، مدين، دائن، الموقع، الجهة، ملاحظة) بتتجمّع لنفس الحساب."""

    def __init__(self, d, memo, ref=None, site_id=None):
        self.date, self.memo, self.ref, self.site_id = d, memo, ref, site_id
        self.lines = defaultdict(float)   # (key, site, party, note, side) → amount

    def dr(self, key, amt, site=None, party=None, note=None):
        if amt and abs(amt) > 0.004:
            self.lines[(key, site, party or None, note or None, "d")] += amt

    def cr(self, key, amt, site=None, party=None, note=None):
        if amt and abs(amt) > 0.004:
            self.lines[(key, site, party or None, note or None, "c")] += amt

    def rows(self):
        out = []
        for (key, site, party, note, side), amt in sorted(self.lines.items(), key=lambda x: (x[0][4], str(x[0]))):
            amt = round(amt, 2)
            if amt < 0:   # مبلغ سالب (مرتجع) يتقلب للجهة التانية
                side, amt = ("c" if side == "d" else "d"), -amt
            if amt:
                out.append((key, amt if side == "d" else 0.0, amt if side == "c" else 0.0, site, party, note))
        dr = round(sum(r[1] for r in out), 2)
        cr = round(sum(r[2] for r in out), 2)
        if out and abs(dr - cr) >= 0.01:   # فرق تقريب صغير جداً يتسوّى على أكبر سطر
            big = max(range(len(out)), key=lambda i: max(out[i][1], out[i][2]))
            k, d_, c_, s, p, n = out[big]
            out[big] = (k, round(d_ + (cr - dr), 2) if d_ else d_, round(c_ + (dr - cr), 2) if c_ else c_, s, p, n)
        return out


def build_auto_entries(db):
    """كل القيود اللي المفروض تكون موجودة من مستندات البرنامج — {(source, source_id): _E}."""
    start = start_date(db)
    E = {}
    # 1) الأرصدة المنقولة من الشركة التانية (لكل موقع): المخزون مدين ← جاري الشركة المنقول منها الأرصدة دائن
    ob = defaultdict(lambda: defaultdict(float))
    ob_date = {}
    for o in db.query(OpeningBalance).options(selectinload(OpeningBalance.item)):
        if o.value:
            ob[o.site_id][_inv(o.item.category if o.item else "spare")] += o.value
            d = o.as_of or start
            ob_date[o.site_id] = min(ob_date.get(o.site_id, d), d)
    for sid, cats in ob.items():
        e = E[("opening", f"site{sid}")] = _E(ob_date[sid], "أرصدة مخزون منقولة من الشركة السابقة", f"موقع {sid}", sid)
        for key, v in cats.items():
            e.dr(key, v, sid)
        e.cr("transferred_co", sum(cats.values()), sid)
    # 2) فواتير الوارد: المخزون + ضريبة المدخلات مدين ← المورد دائن (التحويلات القديمة بين الفروع ← الوسيط)
    for r in (db.query(Receipt).options(selectinload(Receipt.lines).selectinload(ReceiptLine.item))
              .filter(Receipt.date >= start)):
        if not r.lines:
            continue
        e = _E(r.date, f"فاتورة وارد — {r.supplier or 'بدون مورد'}", r.invoice_no, r.site_id)
        for l in r.lines:
            e.dr(_inv(l.item.category if l.item else "spare"), l.total, r.site_id)
        if r.is_transfer:
            e.cr("transit", r.net, r.site_id, r.supplier)
        else:
            e.dr("vat_in", r.vat, r.site_id)
            e.cr("ap", r.gross, r.site_id, r.supplier)
        E[("receipt", str(r.id))] = e
    # 3) الصرف المعتمد: المصروف (حسب نوع الوحدة / جهة المواد الخام) مدين ← المخزون دائن
    reqs = (db.query(Request).options(selectinload(Request.lines).selectinload(RequestLine.item), selectinload(Request.unit))
            .filter(Request.status.in_(["approved", "partial"]), Request.work_date >= start))
    for q in reqs:
        lines = [l for l in q.lines if l.status == "approved" and (l.value or 0)]
        if not lines:
            continue
        unit = q.unit
        to_transit = bool(unit and (unit.name or "").startswith("تحويل"))
        e = _E(q.work_date, f"صرف {'قطع غيار' if q.type == 'spare' else 'مواد خام'} — {unit.name if unit else 'بدون وحدة'}",
               q.req_no, q.site_id)
        for l in lines:
            if to_transit:
                key = "transit"
            elif q.type == "spare":
                kind = unit.kind if unit and unit.kind in ("crusher", "asphalt", "concrete", "transport") else "other"
                key = f"exp_spare_{kind}"
            else:
                key = RAW_DEST_ACC.get(q.raw_dest or "asphalt", "raw_other")
            e.dr(key, l.value, q.site_id, (unit.name if unit else None) if q.raw_dest not in ("sale", "project") else q.customer)
            e.cr(_inv(l.item.category if l.item else q.type), l.value, q.site_id)
        E[("issue", str(q.id))] = e
    # 4) إنتاج الكسارات: مخزون المواد الخام مدين ← الإنتاج المحمّل على المخزون دائن
    for p in (db.query(CrusherProduction).options(selectinload(CrusherProduction.lines), selectinload(CrusherProduction.unit))
              .filter(CrusherProduction.date >= start)):
        v = p.total_value
        if v:
            e = E[("production", str(p.id))] = _E(p.date, f"إنتاج {p.unit.name if p.unit else 'الكسارة'}", f"إنتاج {p.id}", p.site_id)
            e.dr("inv_raw", v, p.site_id)
            e.cr("prod_crusher", v, p.site_id, p.unit.name if p.unit else None)
    # 5) التحويلات بين الفروع: عند الإرسال للوسيط، وعند الاستلام للموقع المستلم (والعجز فروقات مخزون)
    for t in (db.query(Transfer).options(selectinload(Transfer.lines).selectinload(TransferLine.item))
              .filter(Transfer.status != "cancelled", Transfer.date >= start)):
        sent = defaultdict(float)
        for l in t.lines:
            sent[_inv(l.item.category if l.item else "spare")] += l.sent_value
        if not sum(sent.values()):
            continue
        e = E[("transfer", f"{t.id}")] = _E(t.date, f"تحويل مرسل إلى {t.to_site.name if t.to_site else ''}", t.tr_no, t.from_site_id)
        for key, v in sent.items():
            e.cr(key, v, t.from_site_id)
        e.dr("transit", sum(sent.values()), t.from_site_id)
        if t.status == "received":
            got = defaultdict(float)
            for l in t.lines:
                q = l.recv_qty if l.recv_qty is not None else l.qty
                got[_inv(l.item.category if l.item else "spare")] += round((q or 0) * (l.unit_cost or 0), 2)
            d = t.received_at.date() if t.received_at else t.date
            r = E[("transfer", f"{t.id}:r")] = _E(d, f"استلام تحويل من {t.from_site.name if t.from_site else ''}", t.tr_no, t.to_site_id)
            for key, v in got.items():
                r.dr(key, v, t.to_site_id)
            r.dr("inv_diff", sum(sent.values()) - sum(got.values()), t.to_site_id, note="فرق الكمية المستلمة")
            r.cr("transit", sum(sent.values()), t.to_site_id)
    # 6) مستخلصات المبيعات: العملاء مدين ← الإيراد + ضريبة المخرجات دائن (قيد لكل شهر + وحدة)
    groups = defaultdict(list)
    for s in db.query(SalesLine).filter(SalesLine.period >= date(start.year, start.month, 1)):
        groups[(s.period, s.unit)].append(s)
    for (period, unit), rows in groups.items():
        sid_ = f"{period:%Y-%m}|{hashlib.sha1(unit.encode()).hexdigest()[:16]}"   # ثابت وقصير مهما طال اسم الوحدة
        e = E[("sale", sid_)] = _E(period, f"مستخلصات {unit} — {period:%Y-%m}", unit[:100], rows[0].site_id)
        for s in rows:
            e.dr("ar", s.total, s.site_id, s.customer)
            e.cr(f"rev_{s.kind if s.kind in ('crusher', 'asphalt', 'concrete') else 'other'}", s.amount, s.site_id)
            e.cr("vat_out", s.vat, s.site_id)
    return E


def _fingerprint(e, rows):
    raw = json.dumps([e.date.isoformat(), e.memo, e.ref, e.site_id, rows], ensure_ascii=False, default=str)
    return hashlib.sha1(raw.encode()).hexdigest()


def sync(db, force=False):
    """يطابق القيود التلقائية مع المستندات: يضيف الجديد، يحدّث المتغيّر، ويحذف اللي مستنده اتمسح. يرجع عدد التغييرات."""
    if not (force or _STATE["dirty"]):
        return 0
    _STATE["dirty"] = False
    seed_chart(db)
    keys = acc_by_key(db)
    want = build_auto_entries(db)
    have = {(j.source, j.source_id): j for j in db.query(JournalEntry).filter(JournalEntry.source != "manual")}
    changes = 0
    for k, j in have.items():
        if k not in want:
            db.delete(j)
            changes += 1
    for k, e in want.items():
        rows = [(keys[key], d, c, s, p, n) for key, d, c, s, p, n in e.rows() if key in keys]
        if not rows:
            continue
        fp = _fingerprint(e, rows)
        j = have.get(k)
        if j is not None and j.fingerprint == fp:
            continue
        if j is None:
            j = JournalEntry(source=k[0], source_id=k[1])
            db.add(j)
        else:
            j.lines.clear()
        j.date, j.memo, j.ref, j.site_id, j.fingerprint = e.date, e.memo, (e.ref or "")[:100] or None, e.site_id, fp
        for acc, d, c, s, p, n in rows:
            j.lines.append(JournalLine(account_id=acc, debit=d, credit=c, site_id=s, party=(p or None) and p[:200], note=n))
        changes += 1
    db.commit()
    return changes


# ---------------- القيود اليدوية ----------------
def save_manual(db, user, d, memo, rows, entry=None):
    """rows: [(account_id, debit, credit, site_id, party, note)] — لازم متوازن وكل الحسابات قابلة للقيد."""
    check_lock(db, d, "تسجيل قيد")
    rows = [r for r in rows if (r[1] or 0) or (r[2] or 0)]
    if len(rows) < 2:
        raise AccountingError("القيد يحتاج سطرين على الأقل")
    accs = {a.id: a for a in db.query(Account).filter(Account.id.in_([r[0] for r in rows]))}
    for acc_id, dr, cr, *_ in rows:
        a = accs.get(acc_id)
        if not a:
            raise AccountingError("اختر الحساب من الشجرة")
        if a.is_group or not a.active:
            raise AccountingError(f"الحساب «{a.name}» حساب رئيسي أو موقوف — اختر حساباً فرعياً")
        if (dr or 0) < 0 or (cr or 0) < 0 or ((dr or 0) and (cr or 0)):
            raise AccountingError("كل سطر إما مدين أو دائن بمبلغ موجب")
    tdr, tcr = round(sum(r[1] or 0 for r in rows), 2), round(sum(r[2] or 0 for r in rows), 2)
    if abs(tdr - tcr) >= 0.01:
        raise AccountingError(f"القيد غير متوازن: المدين {tdr:,.2f} والدائن {tcr:,.2f}")
    if entry is None:
        entry = JournalEntry(source="manual", created_by_id=user.id)
        db.add(entry)
    else:
        check_lock(db, entry.date, "تعديل قيد")
        entry.lines.clear()
    entry.date, entry.memo = d, memo
    for acc_id, dr, cr, site, party, note in rows:
        entry.lines.append(JournalLine(account_id=acc_id, debit=round(dr or 0, 2), credit=round(cr or 0, 2),
                                       site_id=site, party=party or None, note=note or None))
    db.flush()
    if entry.source_id is None:
        entry.source_id = f"M{entry.id}"
    return entry


# ---------------- الأرصدة والقوائم ----------------
def sums(db, d_from=None, d_to=None, site_id=None):
    """{account_id: (مدين، دائن)} للفترة."""
    q = db.query(JournalLine.account_id, func.sum(JournalLine.debit), func.sum(JournalLine.credit)).join(JournalEntry)
    if d_from:
        q = q.filter(JournalEntry.date >= d_from)
    if d_to:
        q = q.filter(JournalEntry.date <= d_to)
    if site_id:
        q = q.filter(JournalLine.site_id == site_id)
    return {a: (d or 0.0, c or 0.0) for a, d, c in q.group_by(JournalLine.account_id)}


def tree(db):
    """الحسابات مرتبة بالكود مع العمق والأبناء."""
    accs = db.query(Account).order_by(Account.code).all()
    kids = defaultdict(list)
    for a in accs:
        kids[a.parent_id].append(a)
    out = []

    def walk(pid, depth):
        for a in sorted(kids.get(pid, []), key=lambda x: x.code):
            out.append((a, depth))
            walk(a.id, depth + 1)
    walk(None, 0)
    seen = {a.id for a, _ in out}
    out += [(a, 0) for a in accs if a.id not in seen]   # أب محذوف/مش موجود
    return out, kids


def rollup(db, values):
    """يجمع قيم الحسابات الفرعية على آبائها. values: {id: number or tuple}."""
    accs = {a.id: a for a in db.query(Account)}
    tot = defaultdict(lambda: None)
    for aid, v in values.items():
        cur = accs.get(aid)
        while cur is not None:
            old = tot[cur.id]
            tot[cur.id] = v if old is None else (tuple(x + y for x, y in zip(old, v)) if isinstance(v, tuple) else old + v)
            cur = accs.get(cur.parent_id)
    return tot


def trial_balance(db, d_from, d_to, site_id=None):
    before = sums(db, None, d_from.fromordinal(d_from.toordinal() - 1), site_id) if d_from else {}
    period = sums(db, d_from, d_to, site_id)
    vals = {}
    for aid in set(before) | set(period):
        bd, bc = before.get(aid, (0, 0))
        pd, pc = period.get(aid, (0, 0))
        ob = bd - bc
        cb = ob + pd - pc
        vals[aid] = (max(ob, 0), max(-ob, 0), pd, pc, max(cb, 0), max(-cb, 0))
    return vals, rollup(db, vals)


def income_statement(db, d_from, d_to, site_id=None):
    period = sums(db, d_from, d_to, site_id)
    accs = {a.id: a for a in db.query(Account)}
    vals = {}
    for aid, (d, c) in period.items():
        a = accs.get(aid)
        if a and a.type == "revenue":
            vals[aid] = c - d
        elif a and a.type == "expense":
            vals[aid] = d - c
    tot = rollup(db, vals)
    rev = sum(v for aid, v in vals.items() if accs[aid].type == "revenue")
    exp = sum(v for aid, v in vals.items() if accs[aid].type == "expense")
    return vals, tot, rev, exp


def balance_sheet(db, d_to, site_id=None):
    s = sums(db, None, d_to, site_id)
    accs = {a.id: a for a in db.query(Account)}
    vals, profit = {}, 0.0
    for aid, (d, c) in s.items():
        a = accs.get(aid)
        if not a:
            continue
        if a.type in ("revenue", "expense"):
            profit += c - d   # صافي الربح لحد التاريخ بيتضاف لحقوق الملكية
        else:
            vals[aid] = d - c if a.type in NORMAL_DEBIT else c - d
    tot = rollup(db, vals)
    sides = {t: sum(v for aid, v in vals.items() if accs[aid].type == t) for t in ("asset", "liability", "equity")}
    return vals, tot, sides, profit


def ledger(db, account_id, d_from, d_to, site_id=None):
    """سطور الحساب بالرصيد المتراكم (والرصيد قبل الفترة)."""
    a = db.get(Account, account_id)
    sign = 1 if a and a.type in NORMAL_DEBIT else -1
    q = (db.query(JournalLine).join(JournalEntry).options(selectinload(JournalLine.entry), selectinload(JournalLine.site))
         .filter(JournalLine.account_id == account_id))
    if site_id:
        q = q.filter(JournalLine.site_id == site_id)
    ob = 0.0
    if d_from:
        b = (db.query(func.sum(JournalLine.debit), func.sum(JournalLine.credit)).join(JournalEntry)
             .filter(JournalLine.account_id == account_id, JournalEntry.date < d_from))
        if site_id:
            b = b.filter(JournalLine.site_id == site_id)
        bd, bc = b.one()
        ob = sign * ((bd or 0) - (bc or 0))
        q = q.filter(JournalEntry.date >= d_from)
    if d_to:
        q = q.filter(JournalEntry.date <= d_to)
    rows, bal = [], ob
    for l in q.order_by(JournalEntry.date, JournalEntry.id, JournalLine.id).all():
        bal += sign * ((l.debit or 0) - (l.credit or 0))
        rows.append((l, bal))
    return a, ob, rows


def doc_link(entry):
    """رابط المستند اللي طلع منه القيد التلقائي."""
    sid = entry.source_id or ""
    if entry.source == "receipt":
        return f"/receipts/{sid}"
    if entry.source == "issue":
        return f"/requests/{sid}"
    if entry.source == "transfer":
        return f"/transfers/{sid.split(':')[0]}"
    if entry.source == "production":
        return "/production"
    if entry.source == "sale":
        return "/sales"
    if entry.source == "opening":
        return "/admin/opening"
    return None
