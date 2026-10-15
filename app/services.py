"""منطق العمل: الأرصدة ومتوسط التكلفة، ترقيم الطلبات، مسار الاعتماد، الإشعارات."""
import re
from collections import defaultdict
from datetime import date, datetime
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
             "warm": ("رمادي دافئ", "#F0EEEA"), "white": ("أبيض", "#FAFAFB"),
             "sky": ("سماوي فاتح", "#E6F0FA"), "mint": ("أخضر فاتح", "#E7F3EC"), "cream": ("كريمي", "#F7F2E6"),
             "sand": ("رملي", "#EFE6D6"), "rose": ("وردي فاتح", "#F8ECEF"), "lavender": ("بنفسجي فاتح", "#EEEBF8"),
             "dark": ("داكن (Dark)", "#12161C"), "navy": ("كحلي داكن", "#0F1A2B")}
THEME_FONTS = {"Cairo": "Cairo", "IBM Plex Sans Arabic": "IBM Plex Arabic", "Tajawal": "Tajawal", "Almarai": "Almarai"}
THEME_DEFAULT = {"primary": "#2B3440", "bg": "gray", "font": "Cairo"}


def _hex_ok(v):
    import re
    return bool(re.fullmatch(r"#[0-9A-Fa-f]{6}", v or ""))


def _bg_ok(v):
    """الخلفية: من الألوان الجاهزة أو أي لون من لوحة الألوان (#RRGGBB)."""
    return v in THEME_BGS or _hex_ok(v)


def _is_dark(hex_color):
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return (0.299 * r + 0.587 * g + 0.114 * b) < 110


def pick_bg(form):
    """قيمة الخلفية من الفورم: اختيار جاهز، أو «custom» = اللون من اللوحة."""
    bg = form.get("bg") or ""
    return (form.get("bg_custom") or "").strip().upper() if bg == "custom" else bg


def _shade(hex_color, dl, ds=0.0):
    """يفتّح/يغمّق اللون (dl على الإضاءة) ويرجع hex."""
    import colorsys
    r, g, b = (int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5))
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    l = min(0.92, max(0.05, l + dl))
    s = min(1, max(0, s + ds))
    r, g, b = colorsys.hls_to_rgb(h, l, s)
    return "#%02X%02X%02X" % (round(r * 255), round(g * 255), round(b * 255))


def _theme_keys(user_id=None):
    pre = f"u{user_id}:" if user_id else ""
    return {k: pre + k for k in ("primary", "bg", "font")}


def get_theme(db: Session, user_id=None):
    """المظهر الافتراضي (من الإدارة)، ولو المستخدم مختار مظهر لنفسه بيغطي عليه."""
    from .models import AppSetting
    t = dict(THEME_DEFAULT)
    rows = {r.key: r.value for r in db.query(AppSetting).filter(
        AppSetting.key.in_(list(_theme_keys().values()) + (list(_theme_keys(user_id).values()) if user_id else [])))}
    for k, key in _theme_keys().items():
        if key in rows:
            t[k] = rows[key]
    t["personal"] = False
    if user_id:
        for k, key in _theme_keys(user_id).items():
            if key in rows:
                t[k] = rows[key]
                t["personal"] = True
    if not _hex_ok(t["primary"]):
        t["primary"] = THEME_DEFAULT["primary"]
    if not _bg_ok(t["bg"]):
        t["bg"] = THEME_DEFAULT["bg"]
    if t["font"] not in THEME_FONTS:
        t["font"] = THEME_DEFAULT["font"]
    p = t["primary"].upper()
    t.update(brand=p, brand2=_shade(p, 0.08), brand3=_shade(p, 0.20, 0.05),
             brand_rgb=",".join(str(int(p[i:i + 2], 16)) for i in (1, 3, 5)), bg_hex=THEME_BGS[t["bg"]][1] if t["bg"] in THEME_BGS else t["bg"],
             font_url="https://fonts.googleapis.com/css2?family=" + t["font"].replace(" ", "+")
                      + ":wght@400;500;600;700&display=swap")
    t["dark"] = _is_dark(t["bg_hex"])
    t["custom_bg"] = t["bg"] not in THEME_BGS
    return t


def save_theme(db: Session, primary, bg, font, user_id=None):
    """user_id فاضي = المظهر الافتراضي للكل (الإدارة)، وإلا = مظهر المستخدم ده بس."""
    from .models import AppSetting
    if not _hex_ok(primary):
        raise BusinessError("اختر لون صحيح")
    if not _bg_ok(bg) or font not in THEME_FONTS:
        raise BusinessError("اختيار غير صحيح")
    keys = _theme_keys(user_id)
    for k, v in (("primary", primary.upper()), ("bg", bg), ("font", font)):
        row = db.get(AppSetting, keys[k])
        if row:
            row.value = v
        else:
            db.add(AppSetting(key=keys[k], value=v))
    db.commit()


def reset_user_theme(db: Session, user_id):
    from .models import AppSetting
    db.query(AppSetting).filter(AppSetting.key.in_(list(_theme_keys(user_id).values()))).delete(synchronize_session=False)
    db.commit()


# ---------------- الأرصدة ----------------
def stock_table(db: Session, site_id: int, item_ids=None, upto=None):
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
    if upto:   # الرصيد لحد تاريخ معيّن
        q = q.filter(Receipt.date <= upto)
    if item_ids is not None:
        q = q.filter(ReceiptLine.item_id.in_(item_ids))
    for iid, qty, val in q.group_by(ReceiptLine.item_id):
        t[iid]["in_qty"] += qty or 0
        t[iid]["in_val"] += val or 0

    q = (db.query(RequestLine.item_id, RequestLine.status, Request.status,
                  func.sum(RequestLine.qty), func.sum(RequestLine.value))
         .join(Request).filter(Request.site_id == site_id, Request.type == "spare"))
    if upto:
        q = q.filter(Request.work_date <= upto)
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
    if upto:
        q = q.filter(Transfer.date <= upto)
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


# ---------------- مخزن الأصول وأدوات السلامة ----------------
def custody_stock(db: Session, site_id: int):
    """رصيد الأصول وأدوات السلامة: الوارد (فواتير) − المسلّم للموظفين + المرتجع سليم.
    التالف والمفقود ما بيرجعش للرصيد."""
    from .models import CustodyRecord
    t = defaultdict(lambda: dict(in_qty=0.0, in_val=0.0, out_qty=0.0, back_qty=0.0, lost_qty=0.0))
    cats = select(Item.id).where(Item.category.in_(["asset", "safety"]))
    for iid, q, v in (db.query(ReceiptLine.item_id, func.sum(ReceiptLine.qty), func.sum(ReceiptLine.qty * ReceiptLine.unit_price))
                      .join(Receipt).filter(Receipt.site_id == site_id, ReceiptLine.item_id.in_(cats)).group_by(ReceiptLine.item_id)):
        t[iid]["in_qty"] += q or 0
        t[iid]["in_val"] += v or 0
    for r in db.query(CustodyRecord).filter(CustodyRecord.site_id == site_id, CustodyRecord.item_id.isnot(None)):
        x = t[r.item_id]
        x["out_qty"] += r.qty or 0
        if r.returned_at:
            if (r.return_condition or "") == "سليمة":
                x["back_qty"] += r.qty or 0
            else:
                x["lost_qty"] += r.qty or 0
    for x in t.values():
        x["held"] = x["out_qty"] - x["back_qty"] - x["lost_qty"]        # لدى الموظفين الآن
        x["avail"] = x["in_qty"] - x["out_qty"] + x["back_qty"]          # المتاح في المخزن
        x["avg"] = x["in_val"] / x["in_qty"] if x["in_qty"] else 0.0
        x["value"] = x["avail"] * x["avg"]
    return t


# ---------------- المواد الخام ----------------
def crusher_price(db: Session, site_id: int):
    """سعر الطن الثابت لإنتاج كسارات الموقع (بيحدده مدير النظام بس)."""
    from .models import AppSetting
    row = db.get(AppSetting, f"crusher_price:{site_id}")
    try:
        return float(row.value) if row else 0.0
    except ValueError:
        return 0.0


def set_crusher_price(db: Session, site_id: int, price: float):
    from .models import AppSetting
    key = f"crusher_price:{site_id}"
    row = db.get(AppSetting, key)
    if row:
        row.value = str(price)
    else:
        db.add(AppSetting(key=key, value=str(price)))


def raw_stock_table(db: Session, site_id: int, upto=None):
    """أرصدة المواد الخام (كميات): افتتاحي + إنتاج الكسارة + وارد (فواتير) − صرف معتمد، والمعلق في الاعتماد لوحده.
    سعر الوحدة: نواتج الكسارة بالسعر الثابت للموقع، والباقي (زي البيتومين والديزل) بمتوسط سعر الافتتاحي والفواتير."""
    from .models import CrusherProduction, CrusherProductionLine
    t = defaultdict(lambda: dict(open_qty=0.0, open_val=0.0, prod_qty=0.0, in_qty=0.0, in_val=0.0,
                                 out_qty=0.0, pending_qty=0.0, out_crusher=0.0, out_asphalt=0.0, out_concrete=0.0, out_sale=0.0,
                                 out_project=0.0))
    raw_ids = select(Item.id).where(Item.category == "raw")
    for ob in db.query(OpeningBalance).filter(OpeningBalance.site_id == site_id, OpeningBalance.item_id.in_(raw_ids)):
        t[ob.item_id]["open_qty"] += ob.qty or 0
        t[ob.item_id]["open_val"] += ob.value or 0
    for iid, q in (db.query(CrusherProductionLine.item_id, func.sum(CrusherProductionLine.qty)).join(CrusherProduction)
                   .filter(CrusherProduction.site_id == site_id, *([CrusherProduction.date <= upto] if upto else []))
                   .group_by(CrusherProductionLine.item_id)):
        t[iid]["prod_qty"] += q or 0
    for iid, q, v in (db.query(ReceiptLine.item_id, func.sum(ReceiptLine.qty), func.sum(ReceiptLine.qty * ReceiptLine.unit_price))
                      .join(Receipt).filter(Receipt.site_id == site_id, ReceiptLine.item_id.in_(raw_ids),
                                            *([Receipt.date <= upto] if upto else []))
                      .group_by(ReceiptLine.item_id)):
        t[iid]["in_qty"] += q or 0
        t[iid]["in_val"] += v or 0
    for iid, ls, rs, dest, et, sp, q in (db.query(RequestLine.item_id, RequestLine.status, Request.status, Request.raw_dest,
                                                  RequestLine.entity_type, RequestLine.sale_price.isnot(None), func.sum(RequestLine.qty))
                                         .join(Request).filter(Request.site_id == site_id, Request.type == "raw",
                                                               *([Request.work_date <= upto] if upto else []))
                                         .group_by(RequestLine.item_id, RequestLine.status, Request.status, Request.raw_dest,
                                                   RequestLine.entity_type, RequestLine.sale_price.isnot(None))):
        if ls == "approved" and rs in ("approved", "partial"):
            t[iid]["out_qty"] += q or 0
            t[iid]["out_crusher" if dest == "crusher" else "out_" + (dest if dest in ("asphalt", "concrete", "sale", "project") else "asphalt")] += q or 0
            if dest == "crusher":   # صرف الكسارة لجهة: يُضاف إلى عمود البيع أو المشاريع حسب نوع الجهة
                t[iid]["out_sale" if (et == "عميل" or sp) else "out_project"] += q or 0
        elif ls == "pending" and rs == "pending":
            t[iid]["pending_qty"] += q or 0
    price = crusher_price(db, site_id)
    crusher = {i for (i,) in db.query(Item.id).filter(Item.category == "raw", Item.crusher.is_(True))}
    mavg = raw_moving_avg(db, site_id, upto=upto)
    for iid, r in t.items():
        r["cur"] = r["open_qty"] + r["prod_qty"] + r["in_qty"] - r["out_qty"]
        r["available"] = r["cur"] - r["pending_qty"]
        r["avg"] = price if iid in crusher else mavg.get(iid, 0.0)
        r["value"] = r["cur"] * r["avg"]
    return t


def raw_moving_avg(db: Session, site_id: int, upto=None, write=False):
    """المتوسط المرجّح المتحرك للمواد الخام غير نواتج الكسارة (البيتومين، الديزل، ...) كما في أنظمة ERP:
    تُرتَّب الحركات بالتاريخ (الوارد قبل الصرف في اليوم نفسه)، وكل وارد يُعيد حساب المتوسط
    = (قيمة الرصيد + قيمة الوارد) / (كمية الرصيد + كمية الوارد)، وكل صرف يُسعَّر بالمتوسط وقت صرفه.
    write=True يُعيد تسعير سطور الصرف المعتمدة بهذا المتوسط. يرجع dict[item_id] = المتوسط في نهاية الفترة."""
    from .models import Request as Req
    non_crusher = select(Item.id).where(Item.category == "raw", Item.crusher.is_not(True))
    ev = defaultdict(list)   # item_id -> [(date, order, id, kind, qty, price|line)]
    for ob in db.query(OpeningBalance).filter(OpeningBalance.site_id == site_id, OpeningBalance.item_id.in_(non_crusher)):
        ev[ob.item_id].append((date.min, 0, ob.id, "in", ob.qty or 0, ob.value or 0))
    for rl, d in (db.query(ReceiptLine, Receipt.date).join(Receipt)
                  .filter(Receipt.site_id == site_id, ReceiptLine.item_id.in_(non_crusher))):
        ev[rl.item_id].append((d or date.min, 1, rl.id, "in", rl.qty or 0, (rl.qty or 0) * (rl.unit_price or 0)))
    for l, d in (db.query(RequestLine, Req.work_date).join(Req)
                 .filter(Req.site_id == site_id, Req.type == "raw", Req.status.in_(["approved", "partial"]),
                         RequestLine.status == "approved", RequestLine.item_id.in_(non_crusher))):
        ev[l.item_id].append((d or date.min, 2, l.id, "out", l.qty or 0, l))
    out = {}
    for iid, moves in ev.items():
        moves.sort(key=lambda m: (m[0], m[1], m[2]))
        bq = bv = avg = 0.0
        for d, _, _, kind, q, x in moves:
            if upto and d != date.min and d > upto and not write:
                break
            if kind == "in":
                if bq <= 1e-9:      # رصيد صفر أو سالب: المتوسط = سعر الوارد
                    avg = x / q if q else avg
                    bv = bq * avg + x
                else:
                    bv += x
                    avg = bv / (bq + q) if bq + q else avg
                bq += q
            else:
                if write:
                    x.unit_cost = round(avg, 4)
                    x.value = round(avg * q, 2)
                bq -= q
                bv = bq * avg
            if not (upto and d != date.min and d > upto):
                out[iid] = avg
    return out


def revalue_raw(db: Session, site_id: int):
    """إعادة تسعير صرف المواد الخام بالمتوسط المتحرك بعد أي وارد أو صرف أو تعديل بتاريخ سابق."""
    raw_moving_avg(db, site_id, write=True)


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


# ---------------- رسم الأعمدة (لوحة المتابعة) ----------------
MONTHS_AR = ["يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو", "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر",
             "ديسمبر"]


def _nice_step(top, n=4):
    import math
    if top <= 0:
        return 1
    raw = top / n
    mag = 10 ** math.floor(math.log10(raw))
    for k in (1, 2, 2.5, 5, 10):
        if raw <= k * mag:
            return k * mag
    return 10 * mag


def column_chart(values, w=1000, h=210, pad_l=8, pad_r=62, pad_t=22, pad_b=44, bar_max=22):
    """أبعاد رسم أعمدة SVG (سلسلة واحدة) من اليمين للشمال (عربي): أول قيمة على اليمين والأرقام على اليمين.
    أعمدة بعرض ≤24px وطرف علوي مدوّر 4px، وخطوط شبكة بأرقام مقرّبة."""
    step = _nice_step(max(values or [0]))
    top = step * max(1, -(-max(values or [0]) // step))
    ticks = [round(step * i, 2) for i in range(int(round(top / step)) + 1)]
    plot_w, plot_h = w - pad_l - pad_r, h - pad_t - pad_b
    n = max(1, len(values))
    band = plot_w / n
    bw = min(bar_max, band * 0.6)
    bars = []
    for i, v in enumerate(values):
        slot = pad_l + band * (n - 1 - i)  # عكس الاتجاه: القيمة رقم 0 في أقصى اليمين
        bh = plot_h * (v / top) if top else 0
        x = slot + (band - bw) / 2
        y = pad_t + plot_h - bh
        r = min(4, bh, bw / 2)
        path = (f"M{x:.1f},{pad_t + plot_h:.1f} V{y + r:.1f} Q{x:.1f},{y:.1f} {x + r:.1f},{y:.1f} "
                f"H{x + bw - r:.1f} Q{x + bw:.1f},{y:.1f} {x + bw:.1f},{y + r:.1f} V{pad_t + plot_h:.1f} Z") if bh > 0 else ""
        bars.append(dict(path=path, x=x, cx=x + bw / 2, y=y, v=v, hit_x=slot, band=band, bw=bw))
    grid = [dict(y=pad_t + plot_h - plot_h * t / top, v=t) for t in ticks]
    return dict(w=w, h=h, bars=bars, grid=grid, base=pad_t + plot_h, pad_l=pad_l, pad_t=pad_t, plot_h=plot_h, top=top,
                x0=pad_l, x1=w - pad_r, tick_x=w - pad_r + 8,
                max_i=max(range(len(values)), key=lambda i: values[i]) if any(values) else None)


def _bar_path(x, y, bw, bh, r):
    """عمود من (x, y+bh) لفوق، بطرف علوي مدوّر r (r=0 من غير تدوير)."""
    b = y + bh
    if r <= 0:
        return f"M{x:.1f},{b:.1f} V{y:.1f} H{x + bw:.1f} V{b:.1f} Z"
    return (f"M{x:.1f},{b:.1f} V{y + r:.1f} Q{x:.1f},{y:.1f} {x + r:.1f},{y:.1f} "
            f"H{x + bw - r:.1f} Q{x + bw:.1f},{y:.1f} {x + bw:.1f},{y + r:.1f} V{b:.1f} Z")


def stacked_chart(stacks, keys, gap=2, **kw):
    """زي column_chart بس كل عمود مقسوم لأجزاء (لكل مفتاح في keys بالترتيب من تحت لفوق) بفاصل 2px.
    stacks: list of dict(key → value). كل جزء بياخد ترتيب المفتاح (k) عشان اللون يفضل ثابت للفرع."""
    totals = [sum(s.get(k, 0) or 0 for k in keys) for s in stacks]
    c = column_chart(totals, **kw)
    scale = c["plot_h"] / c["top"] if c["top"] else 0
    for b, s in zip(c["bars"], stacks):
        segs, y = [], c["base"]
        parts = [(i, s.get(k, 0) or 0) for i, k in enumerate(keys) if (s.get(k, 0) or 0) > 0]
        for j, (i, v) in enumerate(parts):
            h = v * scale
            top_seg = j == len(parts) - 1
            hh = h - (0 if j == 0 else gap)          # الفاصل بياكل من الجزء اللي فوق
            if hh > 0.3:
                r = min(4, hh, b["bw"] / 2) if top_seg else 0
                segs.append(dict(k=i, path=_bar_path(b["x"], y - h, b["bw"], hh, r)))
            y -= h
        b["segs"] = segs
    return c


def grouped_chart(stacks, keys, gap=2, subs=None, bar_max=22, **kw):
    """أعمدة جنب بعض: لكل شهر عمود لكل مفتاح (فرع) بفاصل 2px — للمقارنة بين الفروع.
    stacks: list of dict(key → value)، أو لو subs متحدد: dict(key → dict(sub → value)) والعمود بيتقسم بالـ subs
    (زي نوع الوحدة جوه كل فرع). كل عمود فيه k (ترتيب الفرع) والأجزاء فيها s (ترتيب الـ sub)."""
    def total(s, k):
        v = s.get(k, 0) or 0
        return sum(x or 0 for x in v.values()) if isinstance(v, dict) else v
    c = column_chart([max([total(s, k) for k in keys] or [0]) for s in stacks], **kw)
    n = max(1, len(keys))
    scale = c["plot_h"] / c["top"] if c["top"] else 0
    for b, s in zip(c["bars"], stacks):
        bw = min(bar_max, (b["band"] * 0.84 - gap * (n - 1)) / n)
        x0 = b["hit_x"] + (b["band"] - (bw * n + gap * (n - 1))) / 2
        cols = []
        for i, k in enumerate(keys):   # أول فرع على اليمين (عربي)
            v = total(s, k)
            x = x0 + (n - 1 - i) * (bw + gap)
            h = v * scale
            parts = []
            if subs and isinstance(s.get(k), dict):
                y = c["base"]
                vals = [(j, s[k].get(sk, 0) or 0) for j, sk in enumerate(subs) if (s[k].get(sk, 0) or 0) > 0]
                for jj, (j, sv) in enumerate(vals):
                    sh = sv * scale
                    hh = sh - (0 if jj == 0 else gap)
                    if hh > 0.3:
                        r = min(4, hh, bw / 2) if jj == len(vals) - 1 else 0
                        parts.append(dict(s=j, v=sv, path=_bar_path(x, y - sh, bw, hh, r)))
                    y -= sh
            cols.append(dict(k=i, v=v, x=x, bw=bw, cx=x + bw / 2, y=c["base"] - h, h=h, parts=parts,
                             path=_bar_path(x, c["base"] - h, bw, h, min(4, h, bw / 2)) if h > 0.3 else ""))
        b["segs"] = cols
    c["grouped"] = True
    return c


# ---------------- الرواكد ----------------
STAGNANT_DAYS = (90, 180, 365)


def cached_stock(db: Session, site_id: int, cache=None):
    """stock_table مرة واحدة لكل موقع في نفس الطلب (cache = dict من الصفحة)."""
    if cache is None:
        return stock_table(db, site_id)
    if site_id not in cache:
        cache[site_id] = stock_table(db, site_id)
    return cache[site_id]


def stagnant_items(db: Session, site_ids, days=180, today=None, cache=None):
    """أصناف قطع غيار ليها رصيد وما اتصرفش منها (لا صرف ولا تحويل صادر) من `days` يوم أو أكتر.
    العد من آخر صرف؛ ولو مفيش صرف خالص: من بداية السنة (أو من أول وارد لو الصنف جديد السنة دي).
    الوارد ما بيصفّرش العداد — ولو جت فواتير وارد بعد آخر صرف الصنف بيتعلّم (added_qty > 0).
    يرجع list of dict(site, item, cur, avg, value, last, since, idle_days, added_qty, added_last) مرتبة بالقيمة."""
    from datetime import date as _date
    today = today or _date.today()
    year_start = _date(today.year, 1, 1)
    out = []
    # من غير «IN (آلاف الأصناف)» في الاستعلامات — أسرع بكتير؛ الفلترة على الأصناف اللي ليها رصيد بتحصل هنا
    spare_items = {i.id: i for i in db.query(Item).filter(Item.category == "spare")}
    for site in db.query(Site).filter(Site.id.in_(list(site_ids))).order_by(Site.name):
        st = cached_stock(db, site.id, cache)
        # الكميات المعلقة (طلبات قيد الاعتماد) محسوبة مصروفة: الباقي بس هو اللي راكد
        live = [iid for iid, r in st.items() if r["available"] > 1e-9]
        if not live:
            continue
        last_out = {}

        def bump(rows):
            for iid, d in rows:
                if d and (iid not in last_out or d > last_out[iid]):
                    last_out[iid] = d
        bump(db.query(RequestLine.item_id, func.max(Request.work_date)).join(Request)
             .filter(Request.site_id == site.id, Request.status.in_(["approved", "partial"]),
                     RequestLine.status == "approved").group_by(RequestLine.item_id))
        bump(db.query(RequestLine.item_id, func.max(Request.work_date)).join(Request)
             .filter(Request.site_id == site.id, Request.status == "pending",
                     RequestLine.status == "pending").group_by(RequestLine.item_id))
        bump(db.query(TransferLine.item_id, func.max(Transfer.date)).join(Transfer)
             .filter(Transfer.status.in_(["in_transit", "received"]),
                     Transfer.from_site_id == site.id).group_by(TransferLine.item_id))
        receipts = defaultdict(list)   # فواتير الوارد
        for iid, d, q in (db.query(ReceiptLine.item_id, Receipt.date, ReceiptLine.qty).join(Receipt)
                          .filter(Receipt.site_id == site.id)):
            receipts[iid].append((d, q or 0))
        first_in = {}                  # أول إضافة (وارد أو تحويل وارد) — للأصناف الجديدة
        for iid, lst in receipts.items():
            first_in[iid] = min(d for d, _ in lst)
        for iid, d in (db.query(TransferLine.item_id, func.min(Transfer.date)).join(Transfer)
                       .filter(Transfer.status == "received", Transfer.to_site_id == site.id).group_by(TransferLine.item_id)):
            if d and (iid not in first_in or d < first_in[iid]):
                first_in[iid] = d
        opening = {iid for (iid,) in db.query(OpeningBalance.item_id)
                   .filter(OpeningBalance.site_id == site.id, OpeningBalance.qty > 0)}
        items = spare_items
        for iid in live:
            it = items.get(iid)
            if not it:
                continue
            if iid in last_out:
                since, added = last_out[iid], [(d, q) for d, q in receipts[iid] if d > last_out[iid]]
            elif iid in opening or iid not in first_in or first_in[iid] <= year_start:
                since, added = year_start, [(d, q) for d, q in receipts[iid] if d >= year_start]
            else:
                since, added = first_in[iid], [(d, q) for d, q in receipts[iid] if d > first_in[iid]]
            idle = (today - since).days
            if idle < days:
                continue
            r = st[iid]
            out.append(dict(site=site, item=it, cur=r["available"], avg=r["avg"], value=r["available"] * r["avg"],
                            last=last_out.get(iid), since=since, idle_days=idle,
                            added_qty=sum(q for _, q in added), added_last=max((d for d, _ in added), default=None)))
    out.sort(key=lambda x: -x["value"])
    return out


def unissued_purchases(db: Session, site_ids, since, cache=None):
    """المشتريات اللي دخلت وما اتصرفتش: الصرف (المعتمد + المعلق في الاعتماد + التحويل الصادر) بيتخصم
    بالأقدمية (الافتتاحي الأول، وبعده الوارد بالتاريخ). يرجع لكل سطر وارد من `since`:
    (site_id, date, قيمة الباقي، قيمة الوارد)."""
    out = []
    for sid in site_ids:
        st = cached_stock(db, sid, cache)
        inflow = defaultdict(list)   # item → [(date, qty, price, is_receipt)]
        for iid, r in st.items():
            if r["open_qty"] > 0:
                inflow[iid].append((None, r["open_qty"], 0, False))
        for iid, d, q, p in (db.query(ReceiptLine.item_id, Receipt.date, ReceiptLine.qty, ReceiptLine.unit_price)
                             .join(Receipt).filter(Receipt.site_id == sid,   # رواكد المشتريات لقطع الغيار بس
                                                   ReceiptLine.item_id.in_(select(Item.id).where(Item.category == "spare")))):
            inflow[iid].append((d, q or 0, p or 0, True))
        for iid, d, q in (db.query(TransferLine.item_id, Transfer.date, TransferLine.recv_qty).join(Transfer)
                          .filter(Transfer.status == "received", Transfer.to_site_id == sid)):
            inflow[iid].append((d, q or 0, 0, False))
        for iid, lst in inflow.items():
            r = st.get(iid)
            used = (r["out_qty"] + r["tr_out_qty"] + r["pending_qty"]) if r else 0
            for d, q, p, is_rec in sorted(lst, key=lambda x: (x[0] is not None, x[0] or datetime.min.date())):
                take = min(q, used)
                used -= take
                if is_rec and d >= since:
                    out.append((sid, d, (q - take) * p, q * p))
    return out


# ---------------- الطلبات ----------------
def next_req_no(db: Session, rtype: str, site: Site, year: int):
    prefix = f"{'SP' if rtype == 'spare' else 'RM'}-{site.code}-{year}-"
    last = db.scalar(select(func.max(Request.req_no)).where(Request.req_no.like(prefix + "%")))
    n = int(last.rsplit("-", 1)[1]) + 1 if last else 1
    return f"{prefix}{n:04d}"


def approvers_for(db: Session, req: Request, stage: int, routes=None):
    routes = [r for r in routes if r.stage == stage] if routes is not None else \
        db.query(ApprovalRoute).filter(ApprovalRoute.stage == stage).all()
    kind = req.unit.kind if req.unit else None

    def site_ok(r):
        return r.site_id is None or r.site_id == req.site_id

    specific = [r for r in routes if site_ok(r) and r.unit_kind and r.unit_kind == kind]
    general = [r for r in routes if site_ok(r) and not r.unit_kind]
    chosen = specific or general
    return [r.user for r in chosen if r.user.active]


def pending_for(db: Session, user: User):
    """الطلبات اللي مستنية اعتماد المستخدم ده — مسار الاعتماد بيتقري مرة واحدة بدل مرة لكل طلب."""
    from sqlalchemy.orm import selectinload
    routes = db.query(ApprovalRoute).options(selectinload(ApprovalRoute.user)).all()
    mine_stages = {r.stage for r in routes if r.user_id == user.id}
    if not mine_stages:
        return []
    reqs = (db.query(Request).options(selectinload(Request.unit))
            .filter(Request.status == "pending", Request.current_stage.in_(mine_stages)).order_by(Request.created_at).all())
    return [r for r in reqs if any(u.id == user.id for u in approvers_for(db, r, r.current_stage, routes))]


def can_approve(db: Session, user: User, req: Request):
    if req.status != "pending":
        return False
    return any(u.id == user.id for u in approvers_for(db, req, req.current_stage))


def create_request(db: Session, *, rtype, site, unit, requester, work_date, reason, lines, asphalt_tons=None, party_tons=None,
                   raw_dest=None, customer=None):
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
    req = Request(req_no=next_req_no(db, rtype, site, work_date.year), type=rtype, site_id=site.id,
                  unit_id=unit.id if unit else None, requester_id=requester.id, work_date=work_date,
                  reason=(reason or "").strip() or None, asphalt_tons=asphalt_tons, status="pending",
                  raw_dest=(raw_dest or "asphalt") if rtype == "raw" else None, customer=customer)
    req.current_stage = req.flow[0]
    if not approvers_for(db, _probe(site, unit), req.current_stage):
        raise BusinessError(f"لا يوجد معتمد مُعرّف لمرحلة «{STAGES[req.current_stage]}» لهذا الطلب — راجع مدير النظام")
    req.site, req.unit, req.requester = site, unit, requester
    from .models import AsphaltPartyTons
    for et, en, t in (party_tons or []):
        req.party_tons.append(AsphaltPartyTons(entity_type=et, entity_name=en, tons=t))
    for l in lines:
        req.lines.append(RequestLine(item_id=l["item"].id, item=l["item"], qty=l["qty"],
                                     entity_type=l.get("entity_type"), entity_name=l.get("entity_name"),
                                     diesel_purpose=l.get("diesel_purpose"), sale_price=l.get("sale_price")))
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
    later = [s for s in req.flow if s > stage]
    if later:
        req.current_stage = later[0]
        db.commit()
        notify_approvers(db, req)
        return req
    # اعتماد نهائي
    if req.type == "spare":
        st = stock_table(db, req.site_id, [l.item_id for l in remaining])
        for l in remaining:
            l.unit_cost = round(st[l.item_id]["avg"], 4)
            l.value = round(l.unit_cost * l.qty, 2)
    else:   # المواد الخام: نواتج الكسارة بالسعر الثابت للموقع، والباقي بمتوسط السعر
        st = raw_stock_table(db, req.site_id)
        for l in remaining:
            l.unit_cost = round(st[l.item_id]["avg"], 4) if l.item_id in st else 0
            l.value = round(l.unit_cost * l.qty, 2)
    for l in remaining:
        l.status = "approved"
    req.status = "partial" if any(l.status == "rejected" for l in req.lines) else "approved"
    req.closed_at = now
    if req.type == "raw":   # التسعير بالمتوسط المتحرك حسب تاريخ الصرف
        db.flush()
        revalue_raw(db, req.site_id)
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
        raise BusinessError("تم إغلاق هذا التحويل مسبقاً")
    short = False
    for l in tr.lines:
        q = recv.get(l.id)
        if q is None or q < 0 or q > l.qty + 1e-9:
            raise BusinessError(f"الكمية المستلمة من «{l.item.name}» يجب أن تكون بين 0 و {fmt(l.qty)}")
        short = short or q < l.qty - 1e-9
    if short and not (note or "").strip():
        raise BusinessError("يوجد نقص في الاستلام — أدخل السبب في الملاحظات")
    for l in tr.lines:
        l.recv_qty = recv[l.id]
    tr.status, tr.received_by_id, tr.received_at = "received", user.id, datetime.now()
    tr.receive_note = (note or "").strip() or None
    db.commit()
    _notify_transfer(db, tr, tr.from_site_id, f"تم استلام التحويل {tr.tr_no} في {tr.to_site.name}")


def cancel_transfer(db: Session, tr: Transfer):
    if tr.status != "in_transit":
        raise BusinessError("لا يمكن الإلغاء — التحويل مغلق")
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


# ---------------- الموردين ----------------
_SUP_PREFIX = re.compile(r"^(شركة|شركه|مؤسسة|مؤسسه|مصنع)\s+")
_SUP_SUFFIX = re.compile(r"\s+(التجارية|التجاريه|للتجارة|للتجاره|المحدودة|المحدوده)$")


def supplier_parts(name):
    """يرجّع (مفتاح المورد الموحّد، المنطقة/الفرع اللي بين القوسين).
    «الشامل الجديد التجارية (صبيا)» و«الشامل الجديد (خميس مشيط)» → نفس المورد «الشامل الجديد»."""
    s = (name or "").strip()
    s = re.split(r"\s+[—–-]\s+", s, maxsplit=1)[0]          # ملاحظة بعد « — »
    region = "، ".join(x.strip() for x in re.findall(r"\(([^)]*)\)", s) if x.strip())
    s = re.sub(r"\([^)]*\)", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    key = s.replace("ة", "ه").replace("ى", "ي").replace("أ", "ا").replace("إ", "ا")
    key = _SUP_PREFIX.sub("", key)
    for _ in range(2):
        key = _SUP_SUFFIX.sub("", key)
    return key.strip(), region, s
