"""النسخة الاحتياطية: ملف ZIP واحد فيه
- data.json  ← كل الجداول (للاسترجاع في البرنامج)
- بيانات.xlsx ← نفس البيانات، شيت لكل جدول (يتفتح بـ Excel)
- مرفقات/     ← الفواتير والصور بأسمائها الأصلية
الاسترجاع بيمسح البيانات الحالية ويرجّع اللي في الملف (لمدير النظام بس)."""
import base64
import io
import json
import tempfile
import zipfile
from datetime import date, datetime

from sqlalchemy import Date, DateTime, LargeBinary, inspect, text

from .db import Base, engine

ATT_TABLE = "attachments"
FORMAT = "warehouse-backup-1"


def _val(v):
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, bytes):
        return base64.b64encode(v).decode()
    return v


def _tables():
    return [t for t in Base.metadata.sorted_tables if t.name in inspect(engine).get_table_names()]


def make_backup():
    """يرجع ملف مؤقت (مفتوح للقراءة) فيه الـ ZIP — بيتكتب على القرص عشان الذاكرة ما تتمليش بالمرفقات."""
    from openpyxl import Workbook
    out = tempfile.TemporaryFile()
    wb = Workbook(write_only=True)
    data = {"format": FORMAT, "created_at": datetime.now().isoformat(), "tables": {}}
    with engine.connect() as conn, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for t in _tables():
            cols = [c.name for c in t.columns]
            if t.name == ATT_TABLE:   # المرفقات: الملف نفسه في فولدر لوحده، والجدول من غير المحتوى
                meta = []
                for row in conn.execute(t.select()).mappings():
                    z.writestr(f"مرفقات/{row['key']}", row["data"] or b"")
                    meta.append({c: _val(row[c]) for c in cols if c != "data"})
                data["tables"][t.name] = meta
                rows = meta
                cols = [c for c in cols if c != "data"]
            else:
                rows = [{c: _val(r[c]) for c in cols} for r in conn.execute(t.select()).mappings()]
                data["tables"][t.name] = rows
            ws = wb.create_sheet(t.name[:31])
            ws.append(cols)
            for r in rows:
                ws.append([r.get(c) for c in cols])
        z.writestr("data.json", json.dumps(data, ensure_ascii=False))
        x = io.BytesIO()
        wb.save(x)
        z.writestr("بيانات.xlsx", x.getvalue())
        z.writestr("اقرأني.txt", "نسخة احتياطية من منظومة المستودعات\n"
                   "- بيانات.xlsx: كل البيانات (شيت لكل جدول) — تتفتح بـ Excel.\n"
                   "- مرفقات: الفواتير والصور.\n"
                   "- data.json: للاسترجاع من داخل البرنامج (الإدارة ← النسخ الاحتياطي).\n")
    out.seek(0)
    return out


def restore_backup(fileobj):
    """يمسح كل البيانات ويرجّع اللي في الملف. يرجع عدد الصفوف المسترجعة."""
    with zipfile.ZipFile(fileobj) as z:
        data = json.loads(z.read("data.json"))
        if data.get("format") != FORMAT:
            raise ValueError("الملف ليس نسخة احتياطية من هذا البرنامج")
        files = {n.split("/", 1)[1]: n for n in z.namelist() if n.startswith("مرفقات/")}
        tables = _tables()
        n = 0
        with engine.begin() as conn:
            for t in reversed(tables):            # المسح بالعكس (الأبناء قبل الآباء)
                conn.execute(t.delete())
            for t in tables:
                rows = data["tables"].get(t.name, [])
                if not rows:
                    continue
                types = {c.name: c.type for c in t.columns}
                fixed = []
                for r in rows:
                    rr = {}
                    for k, v in r.items():
                        if k not in types:
                            continue
                        ty = types[k]
                        if v is not None and isinstance(ty, DateTime):
                            v = datetime.fromisoformat(v)
                        elif v is not None and isinstance(ty, Date):
                            v = date.fromisoformat(v[:10])
                        elif v is not None and isinstance(ty, LargeBinary):
                            v = base64.b64decode(v)
                        rr[k] = v
                    if t.name == ATT_TABLE:
                        rr["data"] = z.read(files[r["key"]]) if r.get("key") in files else b""
                    fixed.append(rr)
                conn.execute(t.insert(), fixed)
                n += len(fixed)
            if engine.dialect.name == "postgresql":   # عدّادات الأرقام تكمل بعد آخر رقم مسترجع
                for t in tables:
                    if "id" in t.columns and t.columns["id"].autoincrement:
                        conn.execute(text(f"SELECT setval(pg_get_serial_sequence('{t.name}', 'id'), "
                                          f"COALESCE((SELECT MAX(id) FROM {t.name}), 0) + 1, false)"))
    return n
