"""Who may run which action.

Every request that reaches `route_request` passes through `authorize` first.
The table below is the single place an action's requirement is written down;
an action missing from it is refused, so a new route added without a decision
fails closed instead of open.

Permissions are the same strings the dashboard stores on `users/{uid}.permissions`
and `firestore.rules` checks. Older user documents still hold the Arabic label a
permission was saved under before persistence moved to the identifier, so both
forms are accepted — the same list `firestore.rules` keeps in `legacyLabel`.

An account whose role is admin passes every permission check: that is what the
dashboard means by an administrator, and the role itself can only be changed
by a holder of `updateUser`.
"""
import os

from flask import jsonify

# --------------------------------------------------------------------- inputs

ADMIN_ROLE_SPELLINGS = frozenset({"admin", "Admin", "ADMIN", "مسؤول النظام"})

# Mirror of `legacyLabel` in firestore.rules. Keep the two in step.
LEGACY_LABELS = {
    'viewDashboard': 'عرض لوحة القيادة',
    'viewBackup': 'عرض النسخ الاحتياطي',
    'createBackup': 'انشاء نسخة احتياطي',
    'restoreBackup': 'استعادة نسخة احتياطي',
    'addProduct': 'اضافة منتج',
    'viewProduct': 'عرض منتج',
    'updateProduct': 'تحديث منتج',
    'deleteProduct': 'حذف منتج',
    'addUser': 'اضافة مستخدم',
    'viewUser': 'عرض مستخدم',
    'updateUser': 'تحديث مستخدم',
    'deleteUser': 'حذف مستخدم',
    'addClient': 'اضافة عميل',
    'viewClient': 'عرض عميل',
    'updateClient': 'تحديث عميل',
    'deleteClient': 'حذف عميل',
    'addPlan': 'اضافة خطة',
    'viewPlan': 'عرض خطة',
    'updatePlan': 'تحديث خطة',
    'deletePlan': 'حذف خطة',
    'addTask': 'اضافة مهمة',
    'viewTask': 'عرض مهمة',
    'updateTask': 'تحديث مهمة',
    'deleteTask': 'حذف مهمة',
    'viewDeals': 'عرض الصفقات ',
    'updateDealState': 'تحديث حالة الصفقة',
    'deleteDeal': 'حذف صفقة',
    'addDeal': 'اضافة صفقة',
    'updateOpportunity': 'تحديث فرصة',
    'deleteOpportunity': 'حذف فرصة',
    'viewOpportunity': 'عرض فرصة',
    'addOpportunity': 'اضافة فرصة',
    'updateAppointment': 'تحديث موعد',
    'deleteAppointment': 'حذف موعد',
    'viewAppointment': 'عرض موعد',
    'addAppointment': 'اضافة موعد',
    'addTechnicalSupport': 'اضافة دعم فني',
    'viewTechnicalSupport': 'عرض دعم فني',
    'updateTechnicalSupport': 'تحديث دعم فني',
    'deleteTechnicalSupport': 'حذف دعم فني',
    'addSupportVisit': 'تسجيل زيارة دعم',
    'updateSupportVisitReport': 'تعديل تقرير زيارة',
    'addDailyReport': 'اضافة تقرير يومي',
    'viewDailyReport': 'عرض تقرير يومي',
    'updateDailyReport': 'تحديث تقرير يومي',
    'deleteDailyReport': 'حذف تقرير يومي',
    'reviewDailyReport': 'مراجعة تقرير يومي',
    'resolveDailyReportProblem': 'حل مشكلة تقرير يومي',
    'viewMaterialTransfer': 'عرض تحويلات المواد',
    'createMaterialTransfer': 'انشاء تحويل مادة',
    'approveMaterialTransfer': 'الموافقة على تحويل مادة',
    'completeMaterialTransfer': 'اكمال تحويل مادة',
    'viewEmailRecipients': 'عرض ايميلات الاشعارات',
    'manageEmailRecipients': 'ادارة ايميلات الاشعارات',
    'viewReviewCenter': 'عرض قائمة المراجعات',
    'restoreDeletedRecord': 'استرجاع سجل محذوف',
    'purgeDeletedRecord': 'حذف نهائي للسجلات',
    'viewProductCatalog': 'عرض البيانات الأساسية للمنتجات',
    'manageProductCatalog': 'إدارة البيانات الأساسية للمنتجات',
    'sendNotification': 'ارسال اشعار',
    'reviewTechnicalReport': 'مراجعة تقرير فني',
    'resolveTechnicalReportProblem': 'حل مشكلة تقرير فني',
}

# ------------------------------------------------------------- requirements

# Runs from Cloud Scheduler with a Google-signed OIDC token, never from a user.
SCHEDULER = "scheduler"
# Any signed-in account that exists and is active.
ACTIVE = "active"
# The caller acting on their own account, or a holder of the listed grants.
# The handler narrows what "own account" may change.
SELF_OR = "self_or"

ACTION_REQUIREMENTS = {
    # Scheduled jobs.
    "daily_notifications": (SCHEDULER,),
    "notify_today_tasks": (SCHEDULER,),
    "notify_tomorrow_tasks": (SCHEDULER,),
    "review_reminders": (SCHEDULER,),
    "role_reminders": (SCHEDULER,),
    "repair_stored_dates": (SCHEDULER,),

    # Users.
    "create": ("addUser",),
    "update": (SELF_OR, "updateUser"),
    "delete": ("deleteUser",),
    "setUserActive": ("updateUser",),
    "setUserEmail": ("updateUser",),
    "resetPassword": ("updateUser",),
    "setPassword": (SELF_OR, "updateUser"),
    "migrateNotificationPreference": ("updateUser",),

    # Clients, plans and the tasks generated from them.
    "deleteClient": ("deleteClient",),
    # Removing a client for good is the review centre's permanent delete.
    "purgeClient": ("purgeDeletedRecord",),
    "createPlanTasks": ("addPlan", "updatePlan"),
    "updatePlanTasks": ("updatePlan",),
    "createTasksFromProduct": ("addPlan", "updatePlan"),
    "syncTasksForClient": ("addClient", "updateClient", "deleteClient"),
    "createTasksForNewClient": ("addClient", "updateClient", "deleteClient"),

    # Backups.
    "listBackups": ("viewBackup",),
    "backupStatus": ("viewBackup",),
    "restoreStatus": ("viewBackup",),
    "manualBackup": ("createBackup",),
    "restoreBackup": ("restoreBackup",),
    "uploadBackupArchive": ("restoreBackup",),
    "downloadBackupArchive": ("restoreBackup",),
    "deleteBackup": ("restoreBackup",),

    # Reads. Firestore already lets every signed-in account read these
    # collections; the server adds the active-account check.
    "getProducts": (ACTIVE,),
    "getPlanProducts": (ACTIVE,),
    "getClients": (ACTIVE,),
    "getStats": (ACTIVE,),
    "getTasks": (ACTIVE,),
    "getTasksByDateRange": (ACTIVE,),
    "getAllTasksStats": (ACTIVE,),
    "getCompletedTasksStatus": (ACTIVE,),
    "getOpportunityStats": (ACTIVE,),

    # Notifications and emails. Every field-app build sends these on ordinary
    # work, so gating them on a grant would silence the current release; they
    # are narrowed in a later phase (SEC-10).
    "sendBusinessNotification": (ACTIVE,),
    "syncTaskReminderSchedule": (ACTIVE,),
    "syncUndatedTaskReminderSchedule": (ACTIVE,),
    "sendNotification": (ACTIVE,),
    "sendNotificationToAll": (ACTIVE,),
    "sendReviewNotification": (ACTIVE,),
    "notifyReviewDecision": (ACTIVE,),
    "sendEmail": (ACTIVE,),
    "notifyReportDeleted": (ACTIVE,),
    "sendDailyReport": (ACTIVE,),
    "notifyNewDeal": (ACTIVE,),
    "sendSupportVisitReport": (ACTIVE,),

    # Special requests: the handlers check the deciding role themselves.
    "reportSaved": (ACTIVE,),
    "decideSpecialRequest": (ACTIVE,),
    "editSpecialRequestNote": (ACTIVE,),
    "retrySpecialRequestSend": (ACTIVE,),

}

# ----------------------------------------------------------------- callers


class Caller:
    """The verified account behind a request, read once per request."""

    def __init__(self, uid, data):
        self.uid = uid
        self.data = data or {}

    @property
    def role(self):
        return str(self.data.get("role") or "").strip()

    @property
    def is_admin(self):
        return self.role in ADMIN_ROLE_SPELLINGS

    @property
    def is_active(self):
        # Only an explicit false deactivates: older documents carry no flag.
        return self.data.get("isActive", True) is not False

    def has(self, permission):
        if self.is_admin:
            return True
        stored = self.data.get("permissions") or []
        if not isinstance(stored, list):
            return False
        label = LEGACY_LABELS.get(permission)
        return permission in stored or (label is not None and label in stored)

    def has_any(self, *permissions):
        return any(self.has(p) for p in permissions)


def _deny(message, status=403):
    return jsonify({"success": False, "error": message}), status


def load_caller(decoded_token, db):
    """The caller's account, or an error response when it may not act at all."""
    uid = decoded_token.get("uid") if decoded_token else None
    if not uid:
        return None, _deny("Unauthorized", 401)
    snapshot = db.collection("users").document(uid).get()
    if not snapshot.exists:
        return None, _deny("Unauthorized")
    caller = Caller(uid, snapshot.to_dict())
    if not caller.is_active:
        return None, _deny("Account is deactivated")
    return caller, None


def target_uid(data):
    """The account a user-management request is about."""
    return (data or {}).get("uid") or (data or {}).get("id")


def check(action, caller, data):
    """None when `caller` may run `action` with `data`, else an error response."""
    requirement = ACTION_REQUIREMENTS.get(action)
    if requirement is None:
        return _deny("Action is not permitted")
    if requirement == (ACTIVE,):
        return None
    if requirement[0] == SCHEDULER:
        return _deny("Action is not permitted")
    if requirement[0] == SELF_OR:
        if target_uid(data) == caller.uid or caller.has_any(*requirement[1:]):
            return None
        return _deny("Permission denied")
    if caller.has_any(*requirement):
        return None
    return _deny("Permission denied")


# ---------------------------------------------------------------- scheduler

# Cloud Scheduler signs each call with an OIDC token for this service account,
# addressed to the function's URL. Both are configuration so a redeploy to a
# new URL does not need a code change; the defaults are this project's values.
SCHEDULER_SERVICE_ACCOUNT = os.getenv(
    "SCHEDULER_SERVICE_ACCOUNT",
    "27531863258-compute@developer.gserviceaccount.com",
)
# Separated by ";" (a "," would split gcloud's --set-env-vars list); "," is
# accepted too.
SCHEDULER_AUDIENCES = [
    a.strip().rstrip("/")
    for a in os.getenv(
        "SCHEDULER_AUDIENCES",
        "https://app-j6xtad2hmq-uc.a.run.app;"
        "https://app-27531863258.us-central1.run.app;"
        "https://us-central1-test-medical-80e1b.cloudfunctions.net/app",
    ).replace(",", ";").split(";")
    if a.strip()
]


def is_scheduler_action(action):
    return ACTION_REQUIREMENTS.get(action) == (SCHEDULER,)


def _bearer(request):
    header = request.headers.get("Authorization") or ""
    return header[len("Bearer "):] if header.startswith("Bearer ") else ""


def verify_scheduler(request, verify=None):
    """None when the request carries a valid Cloud Scheduler OIDC token."""
    token = _bearer(request)
    if not token:
        return _deny("Authorization header missing", 401)
    if verify is None:
        from google.auth.transport import requests as google_requests
        from google.oauth2 import id_token

        def verify(raw):
            return id_token.verify_oauth2_token(raw, google_requests.Request(), audience=None)
    try:
        claims = verify(token)
    except Exception:
        return _deny("Invalid token")
    audience = str(claims.get("aud") or "").rstrip("/")
    if audience not in SCHEDULER_AUDIENCES:
        return _deny("Invalid token")
    if claims.get("email") != SCHEDULER_SERVICE_ACCOUNT or not claims.get("email_verified"):
        return _deny("Invalid token")
    return None


# ------------------------------------------------------------------ entry


def authorize(action, request, db, data, verify_user_token, verify_oidc=None):
    """Gate one request. Returns (caller, None) to proceed, or (None, error).

    `verify_user_token` is `modules.auth.verify_token`, passed in so this module
    stays importable without Firebase credentials.
    """
    if action not in ACTION_REQUIREMENTS:
        return None, _deny("Action is not permitted")
    if is_scheduler_action(action):
        error = verify_scheduler(request, verify_oidc)
        return (None, error) if error else (None, None)

    decoded_token, error, status = verify_user_token(request)
    if error:
        return None, (jsonify(error), status)
    caller, error = load_caller(decoded_token, db)
    if error:
        return None, error
    error = check(action, caller, data)
    if error:
        return None, error
    caller.decoded_token = decoded_token
    return caller, None
