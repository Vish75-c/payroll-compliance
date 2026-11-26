import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client

from payroll.models import Company

pytestmark = pytest.mark.django_db


@pytest.fixture
def credentials():
    values = {"username": "session-tester", "password": "local-test-passphrase"}
    get_user_model().objects.create_user(**values)
    return values


def post(client, path, data, **headers):
    return client.post(path, data, content_type="application/json", **headers)


def test_sign_in_requires_csrf_and_rotates_the_token(credentials):
    browser = Client(enforce_csrf_checks=True)
    session = browser.get("/api/me")
    assert session.json() == {"authenticated": False}
    assert "no-store" in session["Cache-Control"]
    original_token = session.cookies[settings.CSRF_COOKIE_NAME].value

    assert post(browser, "/api/login", credentials).status_code == 403
    assert post(browser, "/api/login", credentials, HTTP_X_CSRFTOKEN=original_token,
                HTTP_ORIGIN="https://untrusted.example").status_code == 403
    assert post(browser, "/api/login", [credentials], HTTP_X_CSRFTOKEN=original_token).status_code == 400
    wrong = dict(credentials, password="wrong-password")
    assert post(browser, "/api/login", wrong, HTTP_X_CSRFTOKEN=original_token).json() == {
        "detail": "Wrong username or password.",
    }

    response = post(browser, "/api/login", credentials, HTTP_X_CSRFTOKEN=original_token)
    assert response.status_code == 200
    assert response.json()["authenticated"] is True
    assert "no-store" in response["Cache-Control"]
    assert response.cookies[settings.SESSION_COOKIE_NAME]["httponly"]
    assert response.cookies[settings.CSRF_COOKIE_NAME].value != original_token
    assert browser.get("/api/me").json()["user"]["username"] == credentials["username"]


def test_logout_invalidates_the_session_and_preserves_permission_errors(credentials):
    browser = Client(enforce_csrf_checks=True)
    company = Company.objects.create(code="session-test", name="Session test", state="KA")
    denied = browser.get("/api/companies")
    assert denied.status_code == 401
    assert denied["WWW-Authenticate"] == "Session"

    token = browser.get("/api/csrf").cookies[settings.CSRF_COOKIE_NAME].value
    response = post(browser, "/api/login", credentials, HTTP_X_CSRFTOKEN=token)
    session_id = response.cookies[settings.SESSION_COOKIE_NAME].value
    current_token = response.cookies[settings.CSRF_COOKIE_NAME].value
    assert browser.get("/api/companies").status_code == 200
    status = browser.patch(f"/api/companies/{company.pk}", {"ai_mode": "off"},
                           content_type="application/json", HTTP_X_CSRFTOKEN=current_token)
    assert status.status_code == 403
    assert status.json()["code"] == "forbidden"
    assert post(browser, "/api/logout", {}, HTTP_X_CSRFTOKEN=token).status_code == 403
    assert post(browser, "/api/logout", {}, HTTP_X_CSRFTOKEN=current_token).status_code == 200
    assert browser.get("/api/me").json() == {"authenticated": False}

    previous_session = Client()
    previous_session.cookies[settings.SESSION_COOKIE_NAME] = session_id
    assert previous_session.get("/api/companies").status_code == 401
