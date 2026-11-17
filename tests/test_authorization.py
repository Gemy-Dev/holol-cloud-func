"""Phase 0: every action is authorized before it runs (modules/authz.py).

Covers the route gate (token, active account, permission, scheduler OIDC) and
the user-management handlers that narrow what a permitted caller may change.
"""
import ast
import pathlib
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from modules import authz
from modules.authz import ACTION_REQUIREMENTS, ACTIVE, SCHEDULER, SELF_OR, authorize

ROOT = pathlib.Path(__file__).resolve().parent.parent

ADMIN_SA = authz.SCHEDULER_SERVICE_ACCOUNT
AUDIENCE = authz.SCHEDULER_AUDIENCES[0]


# ------------------------------------------------------------------ helpers

def _request(token="user-token"):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return SimpleNamespace(headers=headers)


def _verify_ok(uid):
    def verify(_request):
        return {"uid": uid}, None, None
    return verify


def _verify_fails(_request):
    return None, {"error": "Authorization header missing"}, 401


def _status(response):
    """Status of a `(body, status)` error tuple."""
    return response[1]


def _seed_user(db, uid, permissions=(), role="salesRepresentative", **extra):
    db._put("users", uid, {"role": role, "permissions": list(permissions), **extra})


def _routed_actions():
    """Every action string main.route_request compares against."""
    tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
    route = next(n for n in tree.body
                 if isinstance(n, ast.FunctionDef) and n.name == "route_request")
    actions = set()
    for node in ast.walk(route):
        if not isinstance(node, ast.Compare):
            continue
        if not (isinstance(node.left, ast.Name) and node.left.id == "action"):
            continue
        for comparator in node.comparators:
            if isinstance(comparator, ast.Constant):
                actions.add(comparator.value)
            elif isinstance(comparator, (ast.Tuple, ast.List, ast.Set)):
                actions.update(e.value for e in comparator.elts if isinstance(e, ast.Constant))
    return actions


PERMISSION_ACTIONS = sorted(
    a for a, r in ACTION_REQUIREMENTS.items() if r[0] not in (ACTIVE, SCHEDULER, SELF_OR)
)
USER_ACTIONS = sorted(a for a, r in ACTION_REQUIREMENTS.items() if r[0] != SCHEDULER)
SCHEDULER_ACTIONS = sorted(a for a, r in ACTION_REQUIREMENTS.items() if r[0] == SCHEDULER)


# -------------------------------------------------------- table coverage

def test_every_routed_action_has_a_requirement():
    missing = _routed_actions() - set(ACTION_REQUIREMENTS)
    assert not missing, f"actions routed without an authorization decision: {sorted(missing)}"


def test_every_requirement_names_a_routed_action():
    stale = set(ACTION_REQUIREMENTS) - _routed_actions()
    assert not stale, f"requirements for actions main.py no longer routes: {sorted(stale)}"


def test_every_permission_in_the_table_has_a_legacy_label():
    for action, requirement in ACTION_REQUIREMENTS.items():
        for perm in requirement:
            if perm in (ACTIVE, SCHEDULER, SELF_OR):
                continue
            assert perm in authz.LEGACY_LABELS, f"{action}: unknown permission {perm}"


def test_unknown_action_is_refused(db):
    _seed_user(db, "u1", role="admin")
    caller, error = authorize("dropEverything", _request(), db, {}, _verify_ok("u1"))
    assert caller is None and _status(error) == 403


# ------------------------------------------------------------- user token

@pytest.mark.parametrize("action", USER_ACTIONS)
def test_user_actions_need_a_token(db, action):
    caller, error = authorize(action, _request(None), db, {}, _verify_fails)
    assert caller is None and _status(error) == 401


@pytest.mark.parametrize("action", USER_ACTIONS)
def test_unknown_account_is_refused(db, action):
    caller, error = authorize(action, _request(), db, {}, _verify_ok("ghost"))
    assert caller is None and _status(error) == 403


@pytest.mark.parametrize("action", USER_ACTIONS)
def test_deactivated_account_is_refused_even_as_admin(db, action):
    _seed_user(db, "u1", role="admin", isActive=False)
    caller, error = authorize(action, _request(), db, {"uid": "u1"}, _verify_ok("u1"))
    assert caller is None and _status(error) == 403


def test_account_without_active_flag_counts_as_active(db):
    db._put("users", "u1", {"role": "salesRepresentative", "permissions": []})
    caller, error = authorize("getClients", _request(), db, {}, _verify_ok("u1"))
    assert error is None and caller.uid == "u1"


# ------------------------------------------------------------- permissions

@pytest.mark.parametrize("action", PERMISSION_ACTIONS)
def test_permission_actions_refuse_a_caller_without_the_grant(db, action):
    _seed_user(db, "rep", permissions=["viewDashboard"])
    caller, error = authorize(action, _request(), db, {"uid": "someone-else"}, _verify_ok("rep"))
    assert caller is None and _status(error) == 403


@pytest.mark.parametrize("action", PERMISSION_ACTIONS)
def test_permission_actions_admit_each_listed_grant(db, action):
    for perm in ACTION_REQUIREMENTS[action]:
        _seed_user(db, "holder", permissions=[perm])
        caller, error = authorize(action, _request(), db, {"uid": "x"}, _verify_ok("holder"))
        assert error is None, f"{action} refused a holder of {perm}"


@pytest.mark.parametrize("action", PERMISSION_ACTIONS)
def test_permission_actions_admit_the_legacy_arabic_label(db, action):
    perm = ACTION_REQUIREMENTS[action][0]
    _seed_user(db, "old", permissions=[authz.LEGACY_LABELS[perm]])
    caller, error = authorize(action, _request(), db, {"uid": "x"}, _verify_ok("old"))
    assert error is None


@pytest.mark.parametrize("role", sorted(authz.ADMIN_ROLE_SPELLINGS))
def test_admin_role_passes_every_permission_check(db, role):
    _seed_user(db, "boss", role=role)
    for action in PERMISSION_ACTIONS:
        caller, error = authorize(action, _request(), db, {"uid": "x"}, _verify_ok("boss"))
        assert error is None, f"admin ({role}) refused {action}"


def test_backup_grants_are_separate(db):
    _seed_user(db, "viewer", permissions=["viewBackup"])
    allowed = {"listBackups", "backupStatus", "restoreStatus"}
    for action in ("listBackups", "backupStatus", "restoreStatus", "manualBackup",
                   "restoreBackup", "uploadBackupArchive", "downloadBackupArchive",
                   "deleteBackup"):
        _, error = authorize(action, _request(), db, {}, _verify_ok("viewer"))
        assert (error is None) == (action in allowed), action


@pytest.mark.parametrize("action", ["update", "setPassword"])
def test_self_actions_admit_the_account_itself(db, action):
    _seed_user(db, "rep")
    _, error = authorize(action, _request(), db, {"uid": "rep"}, _verify_ok("rep"))
    assert error is None


@pytest.mark.parametrize("action", ["update", "setPassword"])
def test_self_actions_refuse_another_account_without_update_user(db, action):
    _seed_user(db, "rep")
    _, error = authorize(action, _request(), db, {"uid": "victim"}, _verify_ok("rep"))
    assert _status(error) == 403


@pytest.mark.parametrize("action", ["update", "setPassword"])
def test_self_actions_admit_update_user_on_another_account(db, action):
    _seed_user(db, "mgr", permissions=["updateUser"])
    _, error = authorize(action, _request(), db, {"uid": "rep"}, _verify_ok("mgr"))
    assert error is None


# --------------------------------------------------------------- scheduler

def _claims(**overrides):
    claims = {"aud": AUDIENCE, "email": ADMIN_SA, "email_verified": True}
    claims.update(overrides)
    return claims


@pytest.mark.parametrize("action", SCHEDULER_ACTIONS)
def test_scheduler_actions_need_a_token(db, action):
    _, error = authorize(action, _request(None), db, {}, _verify_fails,
                         verify_oidc=lambda _t: _claims())
    assert _status(error) == 401


@pytest.mark.parametrize("action", SCHEDULER_ACTIONS)
def test_scheduler_actions_accept_the_scheduler_token(db, action):
    caller, error = authorize(action, _request("oidc"), db, {}, _verify_fails,
                              verify_oidc=lambda _t: _claims())
    assert error is None and caller is None


def test_scheduler_audience_with_trailing_slash_is_accepted(db):
    _, error = authorize("role_reminders", _request("oidc"), db, {}, _verify_fails,
                         verify_oidc=lambda _t: _claims(aud=AUDIENCE + "/"))
    assert error is None


@pytest.mark.parametrize("claims", [
    _claims(aud="https://elsewhere.example.com"),
    _claims(email="attacker@example.com"),
    _claims(email_verified=False),
])
def test_scheduler_token_for_anyone_else_is_refused(db, claims):
    _, error = authorize("daily_notifications", _request("oidc"), db, {}, _verify_fails,
                         verify_oidc=lambda _t: claims)
    assert _status(error) == 403


def test_a_user_firebase_token_cannot_run_a_scheduled_job(db):
    _seed_user(db, "boss", role="admin")

    def not_google_signed(_token):
        raise ValueError("Wrong issuer")

    _, error = authorize("daily_notifications", _request("firebase"), db, {},
                         _verify_ok("boss"), verify_oidc=not_google_signed)
    assert _status(error) == 403


def test_scheduler_audiences_accept_semicolons_and_commas(monkeypatch):
    import importlib

    monkeypatch.setenv("SCHEDULER_AUDIENCES", "https://a.example/;https://b.example,https://c.example")
    reloaded = importlib.reload(authz)
    try:
        assert reloaded.SCHEDULER_AUDIENCES == [
            "https://a.example", "https://b.example", "https://c.example"]
    finally:
        monkeypatch.delenv("SCHEDULER_AUDIENCES")
        importlib.reload(authz)


# ------------------------------------------------------------ user handlers

def _caller(db, uid):
    return authz.Caller(uid, db.collection("users").document(uid).get().to_dict())


def _body(response):
    if isinstance(response, tuple):
        return response[0].get_json(), response[1]
    return response.get_json(), 200


def test_self_update_cannot_raise_role_or_permissions(db):
    from modules.users import update_user

    _seed_user(db, "rep", permissions=["viewDashboard"], name="Old")
    body, status = _body(update_user(
        {"uid": "rep", "name": "New", "role": "admin", "permissions": ["updateUser"],
         "isActive": True, "status": "x"},
        _caller(db, "rep"), db))
    stored = db._get_all("users")["rep"]
    assert status == 200 and body["success"]
    assert stored["name"] == "New"
    assert stored["role"] == "salesRepresentative"
    assert stored["permissions"] == ["viewDashboard"]
    assert "status" not in stored


def test_update_of_another_account_without_grant_is_refused(db):
    from modules.users import update_user

    _seed_user(db, "rep")
    _seed_user(db, "victim", name="Victim")
    _, status = _body(update_user({"uid": "victim", "name": "x"}, _caller(db, "rep"), db))
    assert status == 403
    assert db._get_all("users")["victim"]["name"] == "Victim"


def test_managed_update_writes_only_the_keys_sent(db):
    from modules.users import update_user

    _seed_user(db, "mgr", permissions=["updateUser"])
    _seed_user(db, "rep", permissions=["addClient"], fcmToken="tok", name="Rep")
    _, status = _body(update_user({"uid": "rep", "role": "salesManager"}, _caller(db, "mgr"), db))
    stored = db._get_all("users")["rep"]
    assert status == 200
    assert stored["role"] == "salesManager"
    # Absent keys are left alone, not reset to empty defaults.
    assert stored["permissions"] == ["addClient"]
    assert stored["fcmToken"] == "tok"
    assert stored["name"] == "Rep"


def test_managed_update_cannot_change_email(db):
    from modules.users import update_user

    _seed_user(db, "mgr", permissions=["updateUser"])
    _seed_user(db, "rep", email="rep@x.com")
    update_user({"uid": "rep", "email": "evil@x.com", "name": "R"}, _caller(db, "mgr"), db)
    assert db._get_all("users")["rep"]["email"] == "rep@x.com"


def test_managed_update_of_is_active_disables_sign_in(db):
    from modules import users

    _seed_user(db, "mgr", permissions=["updateUser"])
    _seed_user(db, "rep")
    with patch.object(users.auth, "update_user") as update, \
            patch.object(users.auth, "revoke_refresh_tokens") as revoke:
        _, status = _body(users.update_user({"uid": "rep", "isActive": False}, _caller(db, "mgr"), db))
    assert status == 200
    update.assert_called_once_with("rep", disabled=True)
    revoke.assert_called_once_with("rep")
    assert db._get_all("users")["rep"]["isActive"] is False


def test_set_user_active_disables_and_revokes(db):
    from modules import users

    _seed_user(db, "mgr", permissions=["updateUser"])
    _seed_user(db, "rep", isActive=True)
    with patch.object(users.auth, "update_user") as update, \
            patch.object(users.auth, "revoke_refresh_tokens") as revoke:
        body, status = _body(users.set_user_active({"uid": "rep", "isActive": False},
                                                   _caller(db, "mgr"), db))
    assert status == 200 and body["isActive"] is False
    update.assert_called_once_with("rep", disabled=True)
    revoke.assert_called_once_with("rep")
    stored = db._get_all("users")["rep"]
    assert stored["isActive"] is False and stored["updatedBy"] == "mgr"


def test_set_user_active_reenables_without_revoking(db):
    from modules import users

    _seed_user(db, "mgr", permissions=["updateUser"])
    _seed_user(db, "rep", isActive=False)
    with patch.object(users.auth, "update_user") as update, \
            patch.object(users.auth, "revoke_refresh_tokens") as revoke:
        _, status = _body(users.set_user_active({"uid": "rep", "isActive": True},
                                                _caller(db, "mgr"), db))
    assert status == 200
    update.assert_called_once_with("rep", disabled=False)
    revoke.assert_not_called()


def test_set_user_active_refuses_self_deactivation(db):
    from modules import users

    _seed_user(db, "mgr", permissions=["updateUser"])
    with patch.object(users.auth, "update_user") as update:
        _, status = _body(users.set_user_active({"uid": "mgr", "isActive": False},
                                                _caller(db, "mgr"), db))
    assert status == 400
    update.assert_not_called()


def test_set_user_active_requires_a_boolean(db):
    from modules import users

    _seed_user(db, "mgr", permissions=["updateUser"])
    _seed_user(db, "rep")
    _, status = _body(users.set_user_active({"uid": "rep", "isActive": "no"},
                                            _caller(db, "mgr"), db))
    assert status == 400


def test_an_account_cannot_be_deleted_only_deactivated(db):
    """Deleting a user is gone: the account is deactivated instead, and keeps
    its link to the tasks and reports it filed."""
    _seed_user(db, "mgr", permissions=["updateUser", "deleteUser"])
    caller, error = authorize("delete", _request(), db, {"uid": "rep"}, _verify_ok("mgr"))
    assert caller is None and _status(error) == 403

def test_create_user_inactive_is_disabled_in_auth(db):
    from modules import users

    with patch.object(users.auth, "create_user", return_value=SimpleNamespace(uid="new")) as create:
        users.create_user({"email": "a@b.com", "password": "12345678", "isActive": False},
                          {"uid": "mgr"}, db)
    assert create.call_args.kwargs["disabled"] is True
    assert db._get_all("users")["new"]["isActive"] is False


# --------------------------------------------------------------- passwords

def _auth_user(uid="rep", email="rep@x.com"):
    return SimpleNamespace(uid=uid, email=email, display_name="Rep")


def test_reset_password_emails_the_link_and_never_returns_it():
    from modules import auth as auth_module
    from modules import email as email_module

    with patch.object(auth_module.auth, "get_user", return_value=_auth_user()), \
            patch.object(auth_module.auth, "generate_password_reset_link",
                         return_value="https://reset/secret"), \
            patch.object(email_module, "send_text_email",
                         return_value={"sent": ["rep@x.com"], "failed": []}) as send:
        body, status = _body(auth_module.reset_password({"uid": "rep"}, {"uid": "mgr"}))
    assert status == 200
    assert "resetLink" not in body
    assert "https://reset/secret" not in str(body)
    recipients, _subject, text = send.call_args.args[:3]
    assert recipients[0]["email"] == "rep@x.com"
    assert "https://reset/secret" in text


def test_reset_password_reports_a_failed_send():
    from modules import auth as auth_module
    from modules import email as email_module

    with patch.object(auth_module.auth, "get_user", return_value=_auth_user()), \
            patch.object(auth_module.auth, "generate_password_reset_link", return_value="l"), \
            patch.object(email_module, "send_text_email",
                         return_value={"sent": [], "failed": [{"email": "rep@x.com"}]}):
        body, status = _body(auth_module.reset_password({"uid": "rep"}, {"uid": "mgr"}))
    assert status == 502
    assert "resetLink" not in body


def test_set_password_for_another_account_needs_update_user(db):
    from modules import auth as auth_module

    _seed_user(db, "rep")
    with patch.object(auth_module.auth, "update_user") as update:
        _, status = _body(auth_module.set_password({"uid": "victim", "password": "123456"},
                                                   _caller(db, "rep")))
    assert status == 403
    update.assert_not_called()


def test_set_password_for_self_is_allowed(db):
    from modules import auth as auth_module

    _seed_user(db, "rep")
    with patch.object(auth_module.auth, "update_user", return_value=_auth_user()) as update:
        _, status = _body(auth_module.set_password({"uid": "rep", "password": "123456"},
                                                   _caller(db, "rep")))
    assert status == 200
    update.assert_called_once_with("rep", password="123456")


def test_invalid_token_error_does_not_echo_the_exception():
    from modules import auth as auth_module

    with patch.object(auth_module.auth, "verify_id_token", side_effect=ValueError("secret detail")):
        _, error, status = auth_module.verify_token(_request("bad"))
    assert status == 403 and "secret detail" not in str(error)


# ------------------------------------------------------- WEB-15: e-mail

def test_set_user_email_changes_auth_then_the_document(db):
    from modules import users

    _seed_user(db, "mgr", permissions=["updateUser"])
    _seed_user(db, "rep", email="old@x.com")
    with patch.object(users.auth, "update_user") as update:
        body, status = _body(users.set_user_email(
            {"uid": "rep", "email": " New@X.com "}, _caller(db, "mgr"), db))
    assert status == 200 and body["email"] == "new@x.com"
    update.assert_called_once_with("rep", email="new@x.com")
    stored = db._get_all("users")["rep"]
    assert stored["email"] == "new@x.com" and stored["updatedBy"] == "mgr"


def test_set_user_email_keeps_the_document_when_auth_refuses(db):
    from modules import users

    _seed_user(db, "mgr", permissions=["updateUser"])
    _seed_user(db, "rep", email="old@x.com")
    taken = users.auth.EmailAlreadyExistsError("taken", None, None)
    with patch.object(users.auth, "update_user", side_effect=taken):
        _, status = _body(users.set_user_email(
            {"uid": "rep", "email": "other@x.com"}, _caller(db, "mgr"), db))
    assert status == 409
    assert db._get_all("users")["rep"]["email"] == "old@x.com"


def test_set_user_email_rejects_a_malformed_address(db):
    from modules import users

    _seed_user(db, "mgr", permissions=["updateUser"])
    _seed_user(db, "rep", email="old@x.com")
    with patch.object(users.auth, "update_user") as update:
        _, status = _body(users.set_user_email(
            {"uid": "rep", "email": "not-an-email"}, _caller(db, "mgr"), db))
    assert status == 400
    update.assert_not_called()


def test_set_user_email_requires_update_user(db):
    _seed_user(db, "rep", email="rep@x.com")
    caller, error = authorize("setUserEmail", _request(), db,
                              {"uid": "rep", "email": "me@x.com"}, _verify_ok("rep"))
    assert caller is None and _status(error) == 403
