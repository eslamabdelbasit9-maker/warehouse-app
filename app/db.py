from fastapi import Request
from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings

_kw = {}
if settings.DATABASE_URL.startswith("sqlite"):
    _kw["connect_args"] = {"check_same_thread": False}
else:
    # Neon بيقفل الاتصالات اللي فاضية: نختبر الاتصال قبل ما نستخدمه، ونسمح بعدد أكبر لما الناس تكتر
    _kw.update(pool_size=10, max_overflow=20, pool_timeout=20, pool_pre_ping=True, pool_recycle=300)

engine = create_engine(settings.DATABASE_URL, future=True, **_kw)

if settings.DATABASE_URL.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_conn, _):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db(request: Request):
    """اتصال واحد لكل طلب — بيتحفظ في request.state عشان أي حاجة تانية في نفس الطلب تستخدمه بدل ما تفتح اتصال جديد."""
    db = SessionLocal()
    request.state.db = db
    try:
        yield db
    finally:
        db.close()
