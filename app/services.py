"""منطق العمل: الأرصدة ومتوسط التكلفة، ترقيم الطلبات، مسار الاعتماد، الإشعارات."""
from collections import defaultdict
from datetime import datetime
from html import escape

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import settings
from .mailer import send_mail
from .models import (STAGES, ApprovalRoute, Item, LineDecision, OpeningBalance,
                     Receipt, ReceiptLine, Request, RequestLine, User, Site)


class BusinessError(Exception):
    pass


def fmt(n, d=2):
    if n is None:
        return ""
    n = round(float(n), d)
    if n == int(n):
        return f"{int(n):,}"
    return f"{n:,.{d}f}".rstrip("0").rstrip(".")


# ---------------- الأرصدة ----------------
def stock_table(db: Session, site_id: int, item_ids=None):
    """يرجع dict[item_id] = {open_qty, open_val, in_qty, in_val, out_qty, pending_qty, cur, avg, value}
    متوسط التكلفة المرجح = (قيمة الافتتاحي + قيمة الوارد) / (كمية الافتتاحي + كمية الوارد)."""
    t = defaultdict(lambda: dict(open_qty=0.0, open_val=0.0, in_qty=0.0, in_val=0.0,
                                 out_qty=0.0, out_val=0.0, pending_qty=0.0, location=None))
    q = db.query(OpeningBalance).filter(OpeningBalance.site_id == site_id)
    if item_ids is not None:
        q = q.filter(OpeningBalance.item_id.in_(item_ids))
    for ob in q:
        r = t[ob.item_id]
        r["open_qty"] += ob.qty or 0
        r["open_val"] += ob.value or 0
        r["location"] = ob.location

    q = (db.query(ReceiptLine.item_id, func.sum(ReceiptLine.qty), func.sum(ReceiptLine.qty * ReceiptLine.unit_price))
         .join(Receipt).filter(Receipt.site_id == site_id))
    if item_ids is not None:
        q = q.filter(ReceiptLine.item_id.in_(item_ids))
    for iid, qty, val in q.group_by(ReceiptLine.item_id):
        t[iid]["in_qty"] += qty or 0
        t[iid]["in_val"] += val or 0

    q = (db.query(RequestLine.item_id, RequestLine.status, Request.status,
                  func.sum(RequestLine.qty), func.sum(RequestLine.value))
         .join(Request).filter(Request.site_id == site_id, Request.type == "spare"))
    if item_ids is not None:
        q = q.filter(RequestLine.item_id.in_(item_ids))
    for iid, lstatus, rstatus, qty, val in q.group_by(RequestLine.item_id, RequestLine.status, Request.status):
        if lstatus == "approved" and rstatus in ("approved", "partial"):
            t[iid]["out_qty"] += qty or 0
            t[iid]["out_val"] += val or 0
        elif lstatus == "pending" and rstatus == "pending":
            t[iid]["pending_qty"] += qty or 0

    for r in t.values():
        base_q = r["open_qty"] + r["in_qty"]
        r["avg"] = (r["open_val"] + r["in_val"]) / base_q if base_q else 0.0
        r["cur"] = r["open_qty"] + r["in_qty"] - r["out_qty"]
        r["available"] = r["cur"] - r["pending_qty"]
        r["value"] = r["cur"] * r["avg"]
    return t


# ---------------- الطلبات ----------------
def next_req_no(db: Session, rtype: str, site: Site, year: int):
    prefix = f"{'SP' if rtype == 'spare' else 'RM'}-{site.code}-{year}-"
    last = db.scalar(select(func.max(Request.req_no)).where(Request.req_no.like(prefix + "%")))
    n = int(last.rsplit("-", 1)[1]) + 1 if last else 1
    return f"{prefix}{n:04d}"


def approvers_for(db: Session, req: Request, stage: int):
    routes = db.query(ApprovalRoute).filter(ApprovalRoute.stage == stage).all()
    kind = req.unit.kind if req.unit else None

    def site_ok(r):
        return r.site_id is None or r.site_id == req.site_id

    specific = [r for r in routes if site_ok(r) and r.unit_kind and r.unit_kind == kind]
    general = [r for r in routes if site_ok(r) and not r.unit_kind]
    chosen = specific or general
    return [r.user for r in chosen if r.user.active]


def can_approve(db: Session, user: User, req: Request):
    if req.status != "pending":
        return False
    return any(u.id == user.id for u in approvers_for(db, req, req.current_stage))


def create_request(db: Session, *, rtype, site, unit, requester, work_date, reason, lines):
    """lines: list of dict(item, qty, entity_type, entity_name, diesel_purpose)"""
    if not lines:
        raise BusinessError("أضف صنفاً واحداً على الأقل")
    if rtype == "spare":
        if not reason or not reason.strip():
            raise BusinessError("السبب إجباري")
        if unit is None:
            raise BusinessError("اختر الوحدة")
        st = stock_table(db, site.id, [l["item"].id for l in lines])
        need = defaultdict(float)
        for l in lines:
            need[l["item"].id] += l["qty"]
        for l in lines:
            avail = st[l["item"].id]["available"] if l["item"].id in st else 0
            if need[l["item"].id] > avail + 1e-9:
                raise BusinessError(f"الكمية المطلوبة من «{l['item'].name}» ({fmt(need[l['item'].id])}) "
                                    f"أكبر من الرصيد المتاح ({fmt(avail)})")
    for l in lines:
        if l["qty"] is None or l["qty"] <= 0:
            raise BusinessError(f"الكمية غير صحيحة للصنف «{l['item'].name}»")
    if not approvers_for(db, _probe(site, unit), 1):
        raise BusinessError("لا يوجد معتمد مُعرّف للمرحلة الأولى لهذه الوحدة — راجع مدير النظام")

    req = Request(req_no=next_req_no(db, rtype, site, work_date.year), type=rtype, site_id=site.id,
                  unit_id=unit.id if unit else None, requester_id=requester.id, work_date=work_date,
                  reason=(reason or "").strip() or None, status="pending", current_stage=1)
    req.site, req.unit, req.requester = site, unit, requester
    for l in lines:
        req.lines.append(RequestLine(item_id=l["item"].id, item=l["item"], qty=l["qty"],
                                     entity_type=l.get("entity_type"), entity_name=l.get("entity_name"),
                                     diesel_purpose=l.get("diesel_purpose")))
    db.add(req)
    db.commit()
    notify_approvers(db, req)
    return req


class _probe:  # كائن مؤقت لحساب المعتمدين قبل إنشاء الطلب
    def __init__(self, site, unit):
        self.site_id, self.unit = site.id, unit


def decide(db: Session, req: Request, user: User, approved_line_ids: set, comment: str | None):
    if not can_approve(db, user, req):
        raise BusinessError("ليس لديك صلاحية اعتماد هذا الطلب في مرحلته الحالية، أو تم البت فيه")
    stage = req.current_stage
    active = [l for l in req.lines if l.status == "pending"]
    if stage == 3 and req.type == "spare":
        approved = [l for l in active if l.id in approved_line_ids]
        st = stock_table(db, req.site_id, [l.item_id for l in approved])
        need = defaultdict(float)
        for l in approved:
            need[l.item_id] += l.qty
        for iid, q in need.items():
            if q > st[iid]["cur"] + 1e-9:
                name = next(l.item.name for l in approved if l.item_id == iid)
                raise BusinessError(f"لا يمكن الاعتماد: رصيد «{name}» الحالي {fmt(st[iid]['cur'])} أقل من {fmt(q)}")
    now = datetime.now()
    for l in active:
        ok = l.id in approved_line_ids
        db.add(LineDecision(request_id=req.id, line_id=l.id, stage=stage, user_id=user.id,
                            decision="approved" if ok else "rejected", comment=(comment or "").strip() or None, at=now))
        if not ok:
            l.status, l.rejected_stage = "rejected", stage

    remaining = [l for l in active if l.status == "pending"]
    if not remaining:
        req.status, req.closed_at = "rejected", now
        db.commit()
        notify_requester(db, req)
        return req
    if stage < 3:
        req.current_stage = stage + 1
        db.commit()
        notify_approvers(db, req)
        return req
    # اعتماد نهائي
    if req.type == "spare":
        st = stock_table(db, req.site_id, [l.item_id for l in remaining])
        for l in remaining:
            l.unit_cost = round(st[l.item_id]["avg"], 4)
            l.value = round(l.unit_cost * l.qty, 2)
    for l in remaining:
        l.status = "approved"
    req.status = "partial" if any(l.status == "rejected" for l in req.lines) else "approved"
    req.closed_at = now
    db.commit()
    notify_requester(db, req)
    return req


# ---------------- الإيميلات ----------------
def _lines_table(req: Request, only_pending=False):
    rows = []
    raw = req.type == "raw"
    for i, l in enumerate(req.lines, 1):
        if only_pending and l.status != "pending":
            continue
        extra = ""
        if raw:
            extra = f"<td>{escape(l.entity_type or '')}</td><td>{escape(l.entity_name or l.diesel_purpose or '')}</td>"
        rows.append(f"<tr><td>{i}</td><td>{escape(l.item.code)}</td><td>{escape(l.item.name)}</td>"
                    f"<td>{fmt(l.qty)} {escape(l.item.uom)}</td>{extra}"
                    + ("" if only_pending else f"<td>{ {'pending':'قيد الاعتماد','approved':'معتمد','rejected':'مرفوض'}[l.status]}</td>")
                    + "</tr>")
    head_extra = "<th>نوع الجهة</th><th>الجهة / الغرض</th>" if raw else ""
    th = "style='background:#0B2A5B;color:#fff;padding:6px;border:1px solid #ccc'"
    return (f"<table dir='rtl' style='border-collapse:collapse;font-family:Tahoma;font-size:13px' border='1' cellpadding='6'>"
            f"<tr><th {th}>#</th><th {th}>الكود</th><th {th}>الصنف</th><th {th}>الكمية</th>"
            f"{head_extra.replace('<th>', f'<th {th}>')}{'' if only_pending else f'<th {th}>الحالة</th>'}</tr>"
            + "".join(rows) + "</table>")


def _wrap(title, body):
    return (f"<div dir='rtl' style='font-family:Tahoma,Arial;font-size:14px;color:#222'>"
            f"<div style='background:#0B2A5B;color:#fff;padding:10px 14px;font-size:16px'>"
            f"<img src='{settings.BASE_URL}/static/logo.png' height='36' alt='' style='vertical-align:middle;margin-left:10px'>"
            f"{escape(settings.APP_NAME)}</div>"
            f"<h3 style='color:#0B2A5B'>{escape(title)}</h3>{body}"
            f"<p style='color:#888;font-size:12px'>رسالة آلية — لا ترد عليها.</p></div>")


def _req_header(req: Request):
    from .models import REQ_TYPES
    return (f"<p><b>رقم الطلب:</b> {escape(req.req_no)} &nbsp; <b>النوع:</b> {REQ_TYPES[req.type]}<br>"
            f"<b>الموقع:</b> {escape(req.site.name)}"
            f"{' &nbsp; <b>الوحدة:</b> ' + escape(req.unit.name) if req.unit else ''}<br>"
            f"<b>التاريخ:</b> {req.work_date:%Y-%m-%d} &nbsp; <b>طالب الصرف:</b> {escape(req.requester_display)}"
            f"{'<br><b>السبب:</b> ' + escape(req.reason) if req.reason else ''}</p>")


def notify_approvers(db: Session, req: Request):
    users = approvers_for(db, req, req.current_stage)
    link = f"{settings.BASE_URL}/approvals/{req.id}"
    btn = (f"<p><a href='{link}' style='background:#0B2A5B;color:#fff;padding:10px 22px;"
           f"text-decoration:none;border-radius:4px;display:inline-block'>مراجعة واعتماد</a></p>")
    html = _wrap(f"طلب بانتظار اعتمادك — مرحلة {STAGES[req.current_stage]}",
                 _req_header(req) + _lines_table(req, only_pending=True) + btn)
    send_mail([u.email for u in users], f"طلب اعتماد {req.req_no} — {req.site.name}", html)


def notify_requester(db: Session, req: Request):
    from .models import REQ_STATUS
    to = [req.requester.email] if req.requester else []
    to += [u.email for u in db.query(User).filter(User.active.is_(True)).all()
           if u.has("storekeeper") and "admin" not in u.role_list and req.site_id in u.site_ids(db)]
    comments = [d for d in req.decisions if d.comment]
    seen, notes = set(), ""
    for d in comments:
        key = (d.stage, d.user_id, d.comment)
        if key not in seen:
            seen.add(key)
            notes += f"<li>{STAGES[d.stage]} ({escape(d.user.name)}): {escape(d.comment)}</li>"
    if notes:
        notes = f"<p><b>ملاحظات المعتمدين:</b></p><ul>{notes}</ul>"
    link = f"{settings.BASE_URL}/requests/{req.id}"
    html = _wrap(f"نتيجة الطلب: {REQ_STATUS[req.status]}",
                 _req_header(req) + _lines_table(req) + notes + f"<p><a href='{link}'>عرض الطلب</a></p>")
    send_mail(to, f"الطلب {req.req_no}: {REQ_STATUS[req.status]}", html)
