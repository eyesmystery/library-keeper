"""Sign a Flask test client in (tests only). Creates the account on first use."""
import auth

PASSWORDS = {"manager": "Manager-pass-1", "staff": "Staff-pass-1"}


class SignedIn:
    """Wraps a test client: adds the CSRF header to every state-changing request."""
    def __init__(self, client, csrf):
        self._c, self.csrf = client, csrf

    def _h(self, kw):
        headers = dict(kw.pop("headers", {}) or {})
        headers.setdefault("X-CSRF-Token", self.csrf)
        kw["headers"] = headers
        return kw

    def get(self, *a, **kw):
        return self._c.get(*a, **kw)

    def post(self, *a, **kw):
        return self._c.post(*a, **self._h(kw))

    def patch(self, *a, **kw):
        return self._c.patch(*a, **self._h(kw))

    def put(self, *a, **kw):
        return self._c.put(*a, **self._h(kw))

    def delete(self, *a, **kw):
        return self._c.delete(*a, **self._h(kw))

    def __getattr__(self, name):
        return getattr(self._c, name)


def login(client, role="manager"):
    username = "test-" + role
    if not any(u["username"] == username for u in auth.list_users()):
        auth.create_user(username, "اختبار " + role, role, PASSWORDS[role])
    r = client.post("/api/admin/login", json={"username": username, "password": PASSWORDS[role]})
    assert r.status_code == 200, r.get_json()
    return SignedIn(client, r.get_json()["csrf"])
