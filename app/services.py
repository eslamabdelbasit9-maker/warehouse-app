"""منطق العمل: الأرصدة ومتوسط التكلفة، ترقيم الطلبات، مسار الاعتماد، الإشعارات."""
from collections import defaultdict
from datetime import datetime
from html import escape

from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import settings
from .mailer import send_mail
from .models import (STAGES, ApprovalRoute, Item, LineDecision, OpeningBalance,
                     Receipt, ReceiptLine, Request, RequestLine, User, Site, Transfer, TransferLine)


class BusinessError(Exception):
    pass


def fmt(n, d=2):
    if n is None:
        return ""
    n = round(float(n), d)
    if n == int(n):
        return f"{int(n):,}"
    return f"{n:,.{d}f}".rstrip("0").rstrip(".")


# ---------------- المظهر (الألوان والخط) ----------------
THEME_COLORS = {  # ألوان جاهزة — والمدير يقدر يختار أي لون تاني
    "#2B3440": "رمادي فحمي (الافتراضي)", "#0B2A5B": "كحلي", "#1D4ED8": "أزرق", "#0F5E56": "أخضر بترولي",
    "#7A1F3D": "عنابي", "#9A3412": "برتقالي محروق",
}
THEME_BGS = {"gray": ("رمادي فاتح", "#ECEEF2"), "light": ("فاتح مزرق", "#F3F5F9"),
             "warm": ("رمادي دافئ", "#F0EEEA"), "white": ("أبيض", "#FAFAFB"), "dark": ("داكن (Dark)", "#12161C")}
THEME_FONTS = {"Cairo": "Cairo", "IBM Plex Sans Arabic": "IBM Plex Arabic", "Tajawal": "Tajawal", "Almarai": "Almarai"}
THEME_DEFAULT = {"primary": "#2B3440", "bg": "gray", "font": "Cairo"}


def _hex_ok(v):
    import re
    return bool(re.fullmatch(r"#[0-9A-Fa-f]{6}", v or ""))


def _shade(hex_color, dl, ds=0.0):
    """يفتّح/يغمّق اللون (dl على الإضاءة) ويرجع hex."""
    import colorsys
    r, g, b = (int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5))
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    l = min(0.92, max(0.05, l + dl))
    s = min(1, max(0, s + ds))
    r, g, b = colorsys.hls_to_rgb(h, l, s)
    return "#%02X%02X%02X" % (round(r * 255), round(g * 255), round(b * 255))


def get_theme(db: Session):
    from .models import AppSetting
    t = dict(THEME_DEFAULT)
    for row in db.query(AppSetting).filter(AppSetting.key.in_(["primary", "bg", "font"])):
        t[row.key] = row.value
    if not _hex_ok(t["primary"]):
        t["primary"] = THEME_DEFAULT["primary"]
    if t["bg"] not in THEME_BGS:
        t["bg"] = THEME_DEFAULT["bg"]
    if t["font"] not in THEME_FONTS:
        t["font"] = THEME_DEFAULT["font"]
    p = t["primary"].upper()
    t.update(brand=p, brand2=_shade(p, 0.08), brand3=_shade(p, 0.20, 0.05),
             brand_rgb=",".join(str(int(p[i:i + 2], 16)) for i in (1, 3, 5)), bg_hex=THEME_BGS[t["bg"]][1],
             font_url="https://fonts.googleapis.com/css2?family=" + t["font"].replace(" ", "+")
                      + ":wght@400;500;600;700&display=swap")
    return t


def save_theme(db: Session, primary, bg, font):
    from .models import AppSetting
    if not _hex_ok(primary):
        raise BusinessError("اختر لون صحيح")
    if bg not in THEME_BGS or font not in THEME_FONTS:
        raise BusinessError("اختيار غير صحيح")
    for k, v in (("primary", primary.upper()), ("bg", bg), ("font", font)):
        row = db.get(AppSetting, k)
        if row:
            row.value = v
        else:
            db.add(AppSetting(key=k, value=v))
    db.commit()


# ---------------- الأرصدة ----------------
def stock_table(db: Session, site_id: int, item_ids=None):
    """يرجع dict[item_id] = {open_qty, open_val, in_qty, in_val, out_qty, pending_qty, cur, avg, value}
    متوسط التكلفة المرجح = (قيمة الافتتاحي + قيمة الوارد) / (كمية الافتتاحي + كمية الوارد)."""
    t = defaultdict(lambda: dict(open_qty=0.0, open_val=0.0, in_qty=0.0, in_val=0.0,
                                 out_qty=0.0, out_val=0.0, pending_qty=0.0, location=None,
                                 tr_in_qty=0.0, tr_in_val=0.0, tr_out_qty=0.0, tr_out_val=0.0))
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

    # التحويلات: الصادر يُخصم من وقت الإرسال (وبعد الاستلام بالكمية المستلمة فقط — الفرق يرجع للمرسِل)،
    # والوارد يُضاف عند تأكيد الاستلام بتكلفة الإرسال.
    q = (db.query(TransferLine.item_id, TransferLine.qty, TransferLine.recv_qty, TransferLine.unit_cost,
                  Transfer.status, Transfer.from_site_id)
         .join(Transfer).filter(Transfer.status.in_(["in_transit", "received"]),
                                (Transfer.from_site_id == site_id) | (Transfer.to_site_id == site_id)))
    if item_ids is not None:
        q = q.filter(TransferLine.item_id.in_(item_ids))
    for iid, qty, recv, cost, status, from_id in q:
        moved = (qty or 0) if status == "in_transit" else (recv or 0)
        if from_id == site_id:
            t[iid]["tr_out_qty"] += moved
            t[iid]["tr_out_val"] += moved * (cost or 0)
        elif status == "received":
            t[iid]["tr_in_qty"] += moved
            t[iid]["tr_in_val"] += moved * (cost or 0)

    for r in t.values():
        base_q = r["open_qty"] + r["in_qty"] + r["tr_in_qty"]
        r["avg"] = (r["open_val"] + r["in_val"] + r["tr_in_val"]) / base_q if base_q else 0.0
        r["cur"] = r["open_qty"] + r["in_qty"] + r["tr_in_qty"] - r["out_qty"] - r["tr_out_qty"]
        r["available"] = r["cur"] - r["pending_qty"]
        r["value"] = r["cur"] * r["avg"]
    return t


def issued_summary(db: Session, site_id: int, item_ids, ref_date, exclude_request_id=None):
    """المنصرف المعتمد من كل صنف في الموقع خلال شهر وسنة ref_date.
    يرجع dict[item_id] = {m_qty, m_n, y_qty, y_n} (n = عدد الطلبات)."""
    from datetime import date as _date
    out = defaultdict(lambda: dict(m_qty=0.0, m_n=0, y_qty=0.0, y_n=0))
    item_ids = list(item_ids or [])
    if not item_ids:
        return out
    q = (db.query(RequestLine.item_id, RequestLine.qty, Request.id, Request.work_date).join(Request)
         .filter(Request.site_id == site_id, Request.type == "spare", Request.status.in_(["approved", "partial"]),
                 RequestLine.status == "approved", RequestLine.item_id.in_(item_ids),
                 Request.work_date >= _date(ref_date.year, 1, 1), Request.work_date <= _date(ref_date.year, 12, 31)))
    if exclude_request_id:
        q = q.filter(Request.id != exclude_request_id)
    m_reqs, y_reqs = defaultdict(set), defaultdict(set)
    for iid, qty, rid, d in q:
        out[iid]["y_qty"] += qty or 0
        y_reqs[iid].add(rid)
        if d.month == ref_date.month:
            out[iid]["m_qty"] += qty or 0
            m_reqs[iid].add(rid)
    for iid in out:
        out[iid]["m_n"], out[iid]["y_n"] = len(m_reqs[iid]), len(y_reqs[iid])
    return out


# ---------------- الرواكد ----------------
STAGNANT_DAYS = (90, 180, 365)


def stagnant_items(db: Session, site_ids, days=180, today=None):
    """أصناف قطع غيار ليها رصيد ومتحركتش (لا صرف ولا وارد ولا تحويل) من `days` يوم أو أكتر.
    يرجع list of dict(site, item, cur, avg, value, last, idle_days) مرتبة بالقيمة."""
    from datetime import date as _date, timedelta
    today = today or _date.today()
    limit = today - timedelta(days=days)
    out = []
    for site in db.query(Site).filter(Site.id.in_(list(site_ids))).order_by(Site.name):
        st = stock_table(db, site.id)
        live = [iid for iid, r in st.items() if r["cur"] > 1e-9]
        if not live:
            continue
        last = defaultdict(lambda: None)

        def bump(rows):
            for iid, d in rows:
                if d and (last[iid] is None or d > last[iid]):
                    last[iid] = d
        bump(db.query(RequestLine.item_id, func.max(Request.work_date)).join(Request)
             .filter(Request.site_id == site.id, Request.status.in_(["approved", "partial"]),
                     RequestLine.status == "approved", RequestLine.item_id.in_(live)).group_by(RequestLine.item_id))
        bump(db.query(ReceiptLine.item_id, func.max(Receipt.date)).join(Receipt)
             .filter(Receipt.site_id == site.id, ReceiptLine.item_id.in_(live)).group_by(ReceiptLine.item_id))
        bump(db.query(TransferLine.item_id, func.max(Transfer.date)).join(Transfer)
             .filter(Transfer.status.in_(["in_transit", "received"]), TransferLine.item_id.in_(live),
                     (Transfer.from_site_id == site.id) | (Transfer.to_site_id == site.id)).group_by(TransferLine.item_id))
        items = {i.id: i for i in db.query(Item).filter(Item.id.in_(live), Item.category == "spare")}
        for iid in live:
            it = items.get(iid)
            if not it or (last[iid] is not None and last[iid] > limit):
                continue
            r = st[iid]
            out.append(dict(site=site, item=it, cur=r["cur"], avg=r["avg"], value=r["value"], last=last[iid],
                            idle_days=(today - last[iid]).days if last[iid] else None))
    out.sort(key=lambda x: -x["value"])
    return out


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


# ---------------- روابط الاعتماد من الإيميل ----------------
# رابط موقّع لكل معتمد: يسمح له بالبت في هذا الطلب في هذه المرحلة فقط بدون تسجيل دخول.
APPROVAL_LINK_DAYS = 14
_signer = URLSafeTimedSerializer(settings.SECRET_KEY, salt="approval-link")


def approval_token(req: Request, user: User) -> str:
    return _signer.dumps({"r": req.id, "s": req.current_stage, "u": user.id})


def read_approval_token(token: str):
    """يرجع (request_id, stage, user_id) أو None إذا كان الرابط غير صالح أو منتهي."""
    try:
        d = _signer.loads(token, max_age=APPROVAL_LINK_DAYS * 86400)
        return int(d["r"]), int(d["s"]), int(d["u"])
    except (BadSignature, KeyError, TypeError, ValueError):
        return None


# ---------------- التحويل بين المواقع ----------------
def next_transfer_no(db: Session, site: Site, year: int):
    prefix = f"TR-{site.code}-{year}-"
    last = db.scalar(select(func.max(Transfer.tr_no)).where(Transfer.tr_no.like(prefix + "%")))
    n = int(last.rsplit("-", 1)[1]) + 1 if last else 1
    return f"{prefix}{n:04d}"


def create_transfer(db: Session, *, from_site, to_site, user, tr_date, note, lines):
    """lines: list of dict(item, qty). يُخصم من المرسِل فوراً بمتوسط سعره الحالي."""
    if not to_site or to_site.id == from_site.id:
        raise BusinessError("اختر الموقع المحوَّل إليه (غير الموقع المرسِل)")
    if not lines:
        raise BusinessError("أضف صنفاً واحداً على الأقل")
    for l in lines:
        if l["qty"] is None or l["qty"] <= 0:
            raise BusinessError(f"الكمية غير صحيحة للصنف «{l['item'].name}»")
    st = stock_table(db, from_site.id, [l["item"].id for l in lines])
    need = defaultdict(float)
    for l in lines:
        need[l["item"].id] += l["qty"]
    for l in lines:
        avail = st[l["item"].id]["available"] if l["item"].id in st else 0
        if need[l["item"].id] > avail + 1e-9:
            raise BusinessError(f"الكمية المحوَّلة من «{l['item'].name}» ({fmt(need[l['item'].id])}) "
                                f"أكبر من الرصيد المتاح في {from_site.name} ({fmt(avail)})")
    tr = Transfer(tr_no=next_transfer_no(db, from_site, tr_date.year), from_site_id=from_site.id, to_site_id=to_site.id,
                  date=tr_date, note=(note or "").strip() or None, created_by_id=user.id, status="in_transit")
    for l in lines:
        tr.lines.append(TransferLine(item_id=l["item"].id, qty=l["qty"],
                                     unit_cost=round(st[l["item"].id]["avg"], 4) if l["item"].id in st else 0))
    db.add(tr)
    db.commit()
    _notify_transfer(db, tr, to_site.id, f"تحويل وارد {tr.tr_no} من {from_site.name} — بانتظار تأكيد الاستلام")
    return tr


def receive_transfer(db: Session, tr: Transfer, user, recv: dict, note: str | None):
    """recv: dict[line_id] = الكمية المستلمة."""
    if tr.status != "in_transit":
        raise BusinessError("التحويل ده اتقفل قبل كده")
    short = False
    for l in tr.lines:
        q = recv.get(l.id)
        if q is None or q < 0 or q > l.qty + 1e-9:
            raise BusinessError(f"الكمية المستلمة من «{l.item.name}» لازم تكون بين 0 و {fmt(l.qty)}")
        short = short or q < l.qty - 1e-9
    if short and not (note or "").strip():
        raise BusinessError("في نقص في الاستلام — اكتب السبب في الملاحظات")
    for l in tr.lines:
        l.recv_qty = recv[l.id]
    tr.status, tr.received_by_id, tr.received_at = "received", user.id, datetime.now()
    tr.receive_note = (note or "").strip() or None
    db.commit()
    _notify_transfer(db, tr, tr.from_site_id, f"تم استلام التحويل {tr.tr_no} في {tr.to_site.name}")


def cancel_transfer(db: Session, tr: Transfer):
    if tr.status != "in_transit":
        raise BusinessError("ما ينفعش يتلغي — التحويل اتقفل")
    tr.status = "cancelled"
    db.commit()


def _notify_transfer(db, tr, site_id, title):
    to = [u.email for u in db.query(User).filter(User.active.is_(True)).all()
          if "storekeeper" in u.role_list and site_id in u.site_ids(db)]
    th = "style='background:#0B2A5B;color:#fff;padding:6px;border:1px solid #ccc'"
    rows = "".join(f"<tr><td>{escape(l.item.code)}</td><td>{escape(l.item.name)}</td><td>{fmt(l.qty)} {escape(l.item.uom)}</td>"
                   f"<td>{fmt(l.recv_qty) if l.recv_qty is not None else ''}</td></tr>" for l in tr.lines)
    body = (f"<p><b>من:</b> {escape(tr.from_site.name)} &nbsp; <b>إلى:</b> {escape(tr.to_site.name)} &nbsp; "
            f"<b>التاريخ:</b> {tr.date:%Y-%m-%d}</p>"
            f"<table dir='rtl' style='border-collapse:collapse;font-family:Tahoma;font-size:13px' border='1' cellpadding='6'>"
            f"<tr><th {th}>الكود</th><th {th}>الصنف</th><th {th}>المُرسَل</th><th {th}>المُستلَم</th></tr>{rows}</table>"
            f"<p><a href='{settings.BASE_URL}/transfers/{tr.id}'>فتح التحويل</a></p>")
    send_mail(to, title, _wrap(title, body))


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
    """إيميل منفصل لكل معتمد فيه رابطه الخاص — يفتحه من Outlook ويعتمد مباشرة."""
    title = f"طلب بانتظار اعتمادك — مرحلة {STAGES[req.current_stage]}"
    body = _req_header(req) + _lines_table(req, only_pending=True)
    btn = ("padding:10px 22px;text-decoration:none;border-radius:4px;display:inline-block;"
           "color:#fff;font-weight:bold;margin-left:8px")
    for u in approvers_for(db, req, req.current_stage):
        link = f"{settings.BASE_URL}/a/{approval_token(req, u)}"
        buttons = (f"<p><a href='{link}?do=approve' style='background:#1e7d32;{btn}'>✔ موافقة</a>"
                   f"<a href='{link}?do=reject' style='background:#b3261e;{btn}'>✖ رفض</a>"
                   f"<a href='{link}' style='background:#0B2A5B;{btn}'>مراجعة الأصناف</a></p>"
                   f"<p style='color:#666;font-size:12px'>الرابط خاص بك ({escape(u.email)}) وصالح {APPROVAL_LINK_DAYS} يوماً "
                   f"ولا يحتاج كلمة مرور — لا تعِد توجيه هذه الرسالة. "
                   f"أو <a href='{settings.BASE_URL}/approvals/{req.id}'>افتح الطلب داخل النظام</a>.</p>")
        send_mail([u.email], f"طلب اعتماد {req.req_no} — {req.site.name}", _wrap(title, body + buttons))


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
