"""صفحة التقارير: حسابات كل تقرير على مستوى المواقع المسموحة للمستخدم والفترة المختارة."""
from collections import defaultdict
from datetime import date

from sqlalchemy.orm import Session, selectinload

from . import services as S
from .models import (CrusherProduction, CrusherProductionLine, Item, Receipt, ReceiptLine, Request, RequestLine,
                     Unit, ITEM_CATS)

DIESEL = "RM-DSL"
OK = ("approved", "partial")

TABS = [("overview", "لوحة الإدارة"), ("crusher", "الكسارات"), ("asphalt", "خلاطات الأسفلت"), ("diesel", "الديزل"),
        ("spare", "قطع الغيار"), ("purchases", "المشتريات والموردين"), ("stock", "المخزون")]


def months_between(d_from, d_to):
    out, y, m = [], d_from.year, d_from.month
    while (y, m) <= (d_to.year, d_to.month):
        out.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _ym(d):
    return (d.year, d.month)


def _raw_lines(db, site_ids, d_from, d_to):
    """سطور صرف المواد الخام المعتمدة في الفترة."""
    return (db.query(RequestLine).join(Request)
            .options(selectinload(RequestLine.request).selectinload(Request.unit), selectinload(RequestLine.item))
            .filter(Request.type == "raw", Request.site_id.in_(site_ids), Request.status.in_(OK),
                    RequestLine.status == "approved", Request.work_date >= d_from, Request.work_date <= d_to).all())


def _asphalt_reqs(db, site_ids, d_from, d_to):
    return (db.query(Request).join(Unit, Request.unit_id == Unit.id)
            .options(selectinload(Request.lines).selectinload(RequestLine.item), selectinload(Request.party_tons))
            .filter(Request.type == "raw", Request.site_id.in_(site_ids), Request.status.in_(OK), Unit.kind == "asphalt",
                    Request.work_date >= d_from, Request.work_date <= d_to).all())


def crusher_data(db, sites, d_from, d_to):
    ids = [s.id for s in sites]
    prod_m, sale_m = defaultdict(float), defaultdict(float)
    prod_site, sale_site, prod_item = defaultdict(float), defaultdict(float), defaultdict(float)
    for pl, d, sid in (db.query(CrusherProductionLine, CrusherProduction.date, CrusherProduction.site_id)
                       .join(CrusherProduction).filter(CrusherProduction.site_id.in_(ids),
                                                       CrusherProduction.date >= d_from, CrusherProduction.date <= d_to)):
        prod_m[_ym(d)] += pl.qty or 0
        prod_site[sid] += pl.qty or 0
        prod_item[pl.item_id] += pl.qty or 0
    customers = defaultdict(lambda: [0.0, 0.0])
    sales_val = 0.0
    for l in _raw_lines(db, ids, d_from, d_to):
        rq = l.request
        if rq.raw_dest == "sale" and l.item.crusher:
            sale_m[_ym(rq.work_date)] += l.qty or 0
            sale_site[rq.site_id] += l.qty or 0
            v = (l.sale_price or 0) * (l.qty or 0)
            sales_val += v
            c = customers[rq.customer or "—"]; c[0] += l.qty or 0; c[1] += v
    items = {i.id: i for i in db.query(Item).filter(Item.id.in_(list(prod_item) or [0]))}
    stock = defaultdict(float)
    for s in sites:
        for iid, r in S.raw_stock_table(db, s.id, upto=d_to).items():
            stock[iid] += r["cur"]
    crusher_items = {i.id: i for i in db.query(Item).filter(Item.category == "raw", Item.crusher.is_(True))}
    return dict(prod_m=prod_m, sale_m=sale_m, prod_site=prod_site, sale_site=sale_site,
                prod_items=sorted(((items[i], q) for i, q in prod_item.items()), key=lambda x: -x[1]),
                customers=sorted(customers.items(), key=lambda x: -x[1][0])[:10], sales_val=sales_val,
                stock=sorted(((crusher_items[i], q) for i, q in stock.items() if i in crusher_items and abs(q) > 0.001),
                             key=lambda x: -x[1]),
                prod=sum(prod_site.values()), sale=sum(sale_site.values()))


def asphalt_data(db, sites, d_from, d_to):
    ids = [s.id for s in sites]
    per_site = defaultdict(lambda: dict(tons=0.0, mat=0.0, dsl_l=0.0, dsl_v=0.0, bit=0.0))
    per_m = defaultdict(lambda: dict(tons=0.0, cost=0.0))
    parties = defaultdict(lambda: dict(tons=0.0, cost=0.0))
    for r in _asphalt_reqs(db, ids, d_from, d_to):
        ps = per_site[r.site_id]; pm = per_m[_ym(r.work_date)]
        ps["tons"] += r.asphalt_tons or 0; pm["tons"] += r.asphalt_tons or 0
        lines = [l for l in r.lines if l.status == "approved"]
        cost_r = 0.0
        for l in lines:
            if l.item.code == DIESEL or l.diesel_purpose:
                ps["dsl_l"] += l.qty or 0; ps["dsl_v"] += l.value or 0
            else:
                ps["mat"] += l.value or 0
                if l.item.code in ("RM-BIT", "RM-PMB"):
                    ps["bit"] += l.qty or 0
            cost_r += l.value or 0
        pm["cost"] += cost_r
        tot_t = sum(p.tons or 0 for p in r.party_tons)
        for p in r.party_tons:
            k = p.entity_name or "—"
            parties[k]["tons"] += p.tons or 0
            if tot_t:
                parties[k]["cost"] += cost_r * (p.tons or 0) / tot_t
    for v in per_site.values():
        v["cost"] = v["mat"] + v["dsl_v"]
        v["per_ton"] = v["cost"] / v["tons"] if v["tons"] else None
        v["l_t"] = v["dsl_l"] / v["tons"] if v["tons"] else None
        v["bit_t"] = 1000 * v["bit"] / v["tons"] if v["tons"] else None   # كجم بيتومين للطن
    tons = sum(v["tons"] for v in per_site.values())
    cost = sum(v["cost"] for v in per_site.values())
    prow = sorted(((k, v["tons"], v["cost"], v["cost"] / v["tons"] if v["tons"] else None) for k, v in parties.items()),
                  key=lambda x: -x[1])
    return dict(per_site=per_site, per_m=per_m, parties=prow[:15], tons=tons, cost=cost,
                per_ton=cost / tons if tons else None,
                l_t=sum(v["dsl_l"] for v in per_site.values()) / tons if tons else None)


def diesel_data(db, sites, d_from, d_to):
    ids = [s.id for s in sites]
    dsl = db.query(Item).filter_by(code=DIESEL).first()
    if not dsl:
        return dict(per_site={}, per_m={}, suppliers=[], in_l=0, out_l=0)
    per_site = defaultdict(lambda: dict(in_l=0.0, out_l=0.0, tons=0.0))
    per_m = defaultdict(lambda: dict(in_l=0.0, out_l=0.0, tons=0.0))
    sup = defaultdict(lambda: [0.0, 0.0])
    for rl, d, sid, s_name in (db.query(ReceiptLine, Receipt.date, Receipt.site_id, Receipt.supplier).join(Receipt)
                               .filter(ReceiptLine.item_id == dsl.id, Receipt.site_id.in_(ids),
                                       Receipt.date >= d_from, Receipt.date <= d_to)):
        per_site[sid]["in_l"] += rl.qty or 0; per_m[_ym(d)]["in_l"] += rl.qty or 0
        x = sup[s_name or "—"]; x[0] += rl.qty or 0; x[1] += (rl.qty or 0) * (rl.unit_price or 0)
    for l in _raw_lines(db, ids, d_from, d_to):
        if l.item_id == dsl.id:
            per_site[l.request.site_id]["out_l"] += l.qty or 0; per_m[_ym(l.request.work_date)]["out_l"] += l.qty or 0
    for r in _asphalt_reqs(db, ids, d_from, d_to):
        per_site[r.site_id]["tons"] += r.asphalt_tons or 0; per_m[_ym(r.work_date)]["tons"] += r.asphalt_tons or 0
    for d in list(per_site.values()) + list(per_m.values()):
        d["l_t"] = d["out_l"] / d["tons"] if d["tons"] else None
    bal = {s.id: S.raw_stock_table(db, s.id, upto=d_to).get(dsl.id, {}).get("cur", 0.0) for s in sites}
    lts = [v["l_t"] for v in per_m.values() if v["l_t"]]
    avg = sum(lts) / len(lts) if lts else None
    return dict(per_site=per_site, per_m=per_m, bal=bal, avg_lt=avg,
                suppliers=sorted(sup.items(), key=lambda x: -x[1][0]),
                in_l=sum(v["in_l"] for v in per_site.values()), out_l=sum(v["out_l"] for v in per_site.values()))


def spare_data(db, sites, d_from, d_to):
    ids = [s.id for s in sites]
    lines = (db.query(RequestLine).join(Request)
             .options(selectinload(RequestLine.request).selectinload(Request.unit), selectinload(RequestLine.item))
             .filter(Request.type == "spare", Request.site_id.in_(ids), Request.status.in_(OK),
                     RequestLine.status == "approved", Request.work_date >= d_from, Request.work_date <= d_to).all())
    units, per_m, items, per_site = defaultdict(float), defaultdict(float), defaultdict(lambda: [None, 0.0, 0.0]), defaultdict(float)
    for l in lines:
        rq = l.request; v = l.value or 0
        units[(rq.site_id, rq.unit.name if rq.unit else "غير محدد")] += v
        per_m[_ym(rq.work_date)] += v
        per_site[rq.site_id] += v
        it = items[l.item_id]; it[0] = l.item; it[1] += l.qty or 0; it[2] += v
    return dict(units=sorted(units.items(), key=lambda x: -x[1]), per_m=per_m, per_site=per_site,
                top=sorted(items.values(), key=lambda x: -x[2])[:10], total=sum(per_site.values()), n=len({l.request_id for l in lines}))


def purchases_data(db, sites, d_from, d_to):
    ids = [s.id for s in sites]
    recs = (db.query(Receipt).options(selectinload(Receipt.lines))
            .filter(Receipt.site_id.in_(ids), Receipt.date >= d_from, Receipt.date <= d_to).all())
    sup = defaultdict(lambda: dict(n=0, net=0.0, vat=0.0, gross=0.0, names=defaultdict(int)))
    per_m = defaultdict(float)
    per_site = defaultdict(float)
    for r in recs:
        if r.is_transfer:
            continue
        key, _, nm = S.supplier_parts(r.supplier)   # نفس توحيد أسماء الموردين في صفحة الموردين
        x = sup[key or "—"]; x["n"] += 1; x["net"] += r.net; x["vat"] += r.vat; x["gross"] += r.gross
        x["names"][nm or "مورد غير محدد"] += 1
        per_m[_ym(r.date)] += r.net
        per_site[r.site_id] += r.net
    for v in sup.values():
        v["name"] = max(v["names"].items(), key=lambda x: (x[1], len(x[0])))[0]
    return dict(suppliers=sorted(sup.items(), key=lambda x: -x[1]["net"]), per_m=per_m, per_site=per_site,
                net=sum(v["net"] for v in sup.values()), vat=sum(v["vat"] for v in sup.values()),
                gross=sum(v["gross"] for v in sup.values()), n=sum(v["n"] for v in sup.values()))


def stock_data(db, sites, d_to):
    by_site = {}
    top = []
    items = {i.id: i for i in db.query(Item)}
    for s in sites:
        cat = defaultdict(float)
        for iid, r in S.raw_stock_table(db, s.id, upto=d_to).items():
            if r["cur"] > 0:
                cat["raw"] += r["value"]; top.append((s, items[iid], r["cur"], r["value"]))
        for iid, r in S.stock_table(db, s.id, upto=d_to).items():
            it = items.get(iid)
            if it and it.category != "raw" and r["cur"] > 0:
                cat[it.category if it.category in ITEM_CATS else "spare"] += r["value"]
                top.append((s, it, r["cur"], r["value"]))
        by_site[s.id] = dict(cat)
    totals = defaultdict(float)
    for d in by_site.values():
        for k, v in d.items():
            totals[k] += v
    return dict(by_site=by_site, totals=dict(totals), total=sum(totals.values()),
                top=sorted(top, key=lambda x: -x[3])[:15])


def overview_data(db, sites, d_from, d_to):
    cr = crusher_data(db, sites, d_from, d_to)
    asp = asphalt_data(db, sites, d_from, d_to)
    sp = spare_data(db, sites, d_from, d_to)
    st = stock_data(db, sites, d_to)
    # مقارنة بالفترة السابقة بنفس الطول
    span = (d_to - d_from).days + 1
    from datetime import timedelta
    p_to = d_from - timedelta(days=1)
    p_from = p_to - timedelta(days=span - 1)
    cr0 = crusher_data(db, sites, p_from, p_to)
    asp0 = asphalt_data(db, sites, p_from, p_to)
    sp0 = spare_data(db, sites, p_from, p_to)
    return dict(cr=cr, asp=asp, sp=sp, st=st, prev=dict(prod=cr0["prod"], sale=cr0["sale"], tons=asp0["tons"],
                                                         per_ton=asp0["per_ton"], l_t=asp0["l_t"], spare=sp0["total"]),
                p_from=p_from, p_to=p_to)


def build(db: Session, tab, sites, d_from, d_to):
    if tab == "crusher":
        return crusher_data(db, sites, d_from, d_to)
    if tab == "asphalt":
        return asphalt_data(db, sites, d_from, d_to)
    if tab == "diesel":
        return diesel_data(db, sites, d_from, d_to)
    if tab == "spare":
        return spare_data(db, sites, d_from, d_to)
    if tab == "purchases":
        return purchases_data(db, sites, d_from, d_to)
    if tab == "stock":
        return stock_data(db, sites, d_to)
    return overview_data(db, sites, d_from, d_to)


def default_period(today=None):
    today = today or date.today()
    return date(today.year, 1, 1), today
