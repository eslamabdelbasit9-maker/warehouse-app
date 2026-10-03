"""إعدادات النظام — كلها تُقرأ من متغيرات البيئة (ملف .env)."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv():
    env = BASE_DIR / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


_load_dotenv()


def _bool(name, default=False):
    return os.getenv(name, str(default)).lower() in ("1", "true", "yes", "on")


class Settings:
    APP_NAME = os.getenv("APP_NAME", "منظومة المستودعات")
    # على Render يُؤخذ العنوان تلقائياً من RENDER_EXTERNAL_URL إذا لم يُحدَّد BASE_URL
    BASE_URL = (os.getenv("BASE_URL") or os.getenv("RENDER_EXTERNAL_URL") or "http://localhost:8000").rstrip("/")
    SECRET_KEY = os.getenv("SECRET_KEY", "change-me-in-production")
    DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'data' / 'app.db'}")
    for _p in ("postgres://", "postgresql://"):
        if DATABASE_URL.startswith(_p):
            DATABASE_URL = "postgresql+psycopg://" + DATABASE_URL[len(_p):]
    UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", str(BASE_DIR / "data" / "uploads")))

    # تسجيل دخول تجريبي (للتطوير فقط) — يجب أن يكون false في التشغيل الفعلي
    DEV_AUTH = _bool("DEV_AUTH", False)

    # أول مدير نظام يُنشأ تلقائياً عند أول تشغيل
    BOOTSTRAP_ADMIN_EMAIL = os.getenv("BOOTSTRAP_ADMIN_EMAIL", "admin@example.com")
    BOOTSTRAP_ADMIN_NAME = os.getenv("BOOTSTRAP_ADMIN_NAME", "مدير النظام")
    BOOTSTRAP_ADMIN_PASSWORD = os.getenv("BOOTSTRAP_ADMIN_PASSWORD", "")

    # Microsoft Entra ID (تسجيل الدخول بحساب الشركة)
    ENTRA_TENANT_ID = os.getenv("ENTRA_TENANT_ID", "")
    ENTRA_CLIENT_ID = os.getenv("ENTRA_CLIENT_ID", "")
    ENTRA_CLIENT_SECRET = os.getenv("ENTRA_CLIENT_SECRET", "")

    # الإيميل: brevo | graph | smtp | outbox (outbox = يُحفظ داخل النظام فقط للتجربة)
    MAIL_MODE = os.getenv("MAIL_MODE", "outbox")
    MAIL_SENDER = os.getenv("MAIL_SENDER", "")  # صندوق الإرسال مثل warehouse@company.com
    MAIL_SENDER_NAME = os.getenv("MAIL_SENDER_NAME", "منظومة المستودعات")
    BREVO_API_KEY = os.getenv("BREVO_API_KEY", "")
    SMTP_HOST = os.getenv("SMTP_HOST", "smtp.office365.com")
    SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USER = os.getenv("SMTP_USER", "")
    SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")


settings = Settings()
settings.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
