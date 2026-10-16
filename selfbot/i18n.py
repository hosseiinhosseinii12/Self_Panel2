"""English + Persian translations. Every user-facing string lives here."""
from .config import CONFIG

STRINGS = {
    "en": {
        "welcome": "Welcome to SELF BOT.",
        "owner_only": "This command is owner-only.",
        "banned": "You are banned.",
        "rate_limited": "Too many messages. Slow down.",
        "main_panel": "Main panel",
        "btn_on": "🟢 On",
        "btn_off": "🔴 Off",
        "btn_status": "📊 Status",
        "btn_settings": "⚙️ Settings",
        "btn_appearance": "🎨 Appearance",
        "btn_jobs": "🔁 Repeat jobs",
        "btn_account": "💎 Account",
        "btn_help": "❓ Help",
        "btn_back": "⬅️ Back",
        "status_on": "Clock is ON",
        "status_off": "Clock is OFF",
        "interval": "Interval",
        "timezone": "Timezone",
        "language": "Language",
        "base_name": "Base name",
        "diamonds": "Diamonds",
        "subscription": "Subscription",
        "plan": "Plan",
        "days_left": "Days left",
        "insufficient": "Insufficient diamonds. Need {need}, you have {have}.",
        "cost": "Cost: {cost} 💎",
        "job_created": "Job #{id} created.",
        "job_stopped": "Job stopped.",
        "no_jobs": "No active jobs.",
        "referral_applied": "Referral applied. +{bonus} 💎 to both sides.",
        "referral_invalid": "Invalid or already-used referral code.",
        "your_referral": "Your referral code: {code}",
    },
    "fa": {
        "welcome": "به سلف بات خوش آمدید.",
        "owner_only": "این دستور فقط برای مالک است.",
        "banned": "شما مسدود شده‌اید.",
        "rate_limited": "تعداد پیام‌ها زیاد است. آرام‌تر.",
        "main_panel": "پنل اصلی",
        "btn_on": "🟢 روشن",
        "btn_off": "🔴 خاموش",
        "btn_status": "📊 وضعیت",
        "btn_settings": "⚙️ تنظیمات",
        "btn_appearance": "🎨 ظاهر",
        "btn_jobs": "🔁 کارهای تکرارشونده",
        "btn_account": "💎 حساب",
        "btn_help": "❓ راهنما",
        "btn_back": "⬅️ بازگشت",
        "status_on": "ساعت روشن است",
        "status_off": "ساعت خاموش است",
        "interval": "فاصله",
        "timezone": "منطقه زمانی",
        "language": "زبان",
        "base_name": "نام پایه",
        "diamonds": "الماس",
        "subscription": "اشتراک",
        "plan": "پلن",
        "days_left": "روزهای باقی‌مانده",
        "insufficient": "الماس کافی نیست. نیاز: {need}، موجودی: {have}.",
        "cost": "هزینه: {cost} 💎",
        "job_created": "کار #{id} ساخته شد.",
        "job_stopped": "کار متوقف شد.",
        "no_jobs": "کار فعالی وجود ندارد.",
        "referral_applied": "کد معرف اعمال شد. +{bonus} 💎 برای هر دو طرف.",
        "referral_invalid": "کد معرف نامعتبر یا قبلاً استفاده شده.",
        "your_referral": "کد معرف شما: {code}",
    },
}


def t(key: str, **kwargs) -> str:
    lang = CONFIG.get("language", "en")
    if lang not in STRINGS:
        lang = "en"
    text = STRINGS[lang].get(key) or STRINGS["en"].get(key) or key
    if kwargs:
        try:
            return text.format(**kwargs)
        except Exception:
            return text
    return text