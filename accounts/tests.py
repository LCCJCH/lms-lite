import os
from unittest import mock

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse

from .roles import ADMIN, APPLICANT, LAB_STAFF, get_role

INVITE = "test-invite"


def make_user(username, *groups, **extra):
    user = User.objects.create_user(username=username, password="pw-12345-xyz", **extra)
    for name in groups:
        user.groups.add(Group.objects.get_or_create(name=name)[0])
    return user


@mock.patch.dict(os.environ, {"DEMO_INVITE_CODE": INVITE})
class RegisterTests(TestCase):
    url = reverse("register")

    def post(self, **overrides):
        data = {
            "username": "newbie",
            "email": "newbie@example.com",
            "first_name": "新同學",
            "password1": "Sample-pass-2026",
            "password2": "Sample-pass-2026",
            "invite_code": INVITE,
        }
        data.update(overrides)
        return self.client.post(self.url, data)

    def test_correct_invite_creates_applicant_only(self):
        resp = self.post()
        self.assertRedirects(resp, reverse("dashboard"), fetch_redirect_response=False)
        user = User.objects.get(username="newbie")
        self.assertEqual(list(user.groups.values_list("name", flat=True)), [APPLICANT])
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertEqual(user.first_name, "新同學")
        # 註冊後自動登入，落在申請者主頁
        resp = self.client.get(reverse("dashboard"))
        self.assertRedirects(resp, reverse("lab:applicant_dashboard"))

    def test_wrong_invite_rejected(self):
        resp = self.post(invite_code="wrong")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "邀請碼不正確")
        self.assertFalse(User.objects.filter(username="newbie").exists())

    def test_blank_invite_rejected(self):
        self.post(invite_code="")
        self.assertFalse(User.objects.filter(username="newbie").exists())

    def test_unset_invite_env_rejects_everything(self):
        with mock.patch.dict(os.environ, {"DEMO_INVITE_CODE": ""}):
            resp = self.post()
        self.assertContains(resp, "目前不開放註冊")
        self.assertFalse(User.objects.filter(username="newbie").exists())

    def test_role_fields_in_post_are_ignored(self):
        admin_group = Group.objects.create(name=ADMIN)
        self.post(groups=[admin_group.pk], is_staff="on", is_superuser="on")
        user = User.objects.get(username="newbie")
        self.assertEqual(list(user.groups.values_list("name", flat=True)), [APPLICANT])
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)

    def test_form_has_no_role_field(self):
        resp = self.client.get(self.url)
        for name in ("groups", "is_staff", "role"):
            self.assertNotIn(name, resp.context["form"].fields)


class RoleTests(TestCase):
    def test_single_groups(self):
        self.assertEqual(get_role(make_user("a", ADMIN)), ADMIN)
        self.assertEqual(get_role(make_user("l", LAB_STAFF)), LAB_STAFF)
        self.assertEqual(get_role(make_user("p", APPLICANT)), APPLICANT)

    def test_priority(self):
        self.assertEqual(get_role(make_user("al", APPLICANT, LAB_STAFF)), LAB_STAFF)
        self.assertEqual(get_role(make_user("all", APPLICANT, LAB_STAFF, ADMIN)), ADMIN)

    def test_superuser_is_admin(self):
        su = User.objects.create_superuser("root", "root@example.com", "pw-12345-xyz")
        self.assertEqual(get_role(su), ADMIN)

    def test_no_group(self):
        self.assertIsNone(get_role(make_user("nobody")))


class LoginRedirectTests(TestCase):
    def login_lands_on(self, user, expected):
        resp = self.client.post(
            reverse("login"), {"username": user.username, "password": "pw-12345-xyz"}, follow=True
        )
        self.assertEqual(resp.request["PATH_INFO"], reverse(expected))
        self.assertEqual(resp.status_code, 200)

    def test_each_role_lands_on_own_dashboard(self):
        self.login_lands_on(make_user("a", ADMIN, is_staff=True), "lab:admin_dashboard")
        self.client.logout()
        self.login_lands_on(make_user("l", LAB_STAFF), "lab:lab_dashboard")
        self.client.logout()
        self.login_lands_on(make_user("p", APPLICANT), "lab:applicant_dashboard")

    def test_superuser_lands_on_admin_dashboard(self):
        User.objects.create_superuser("root", "root@example.com", "pw-12345-xyz")
        resp = self.client.post(
            reverse("login"), {"username": "root", "password": "pw-12345-xyz"}, follow=True
        )
        self.assertEqual(resp.request["PATH_INFO"], reverse("lab:admin_dashboard"))

    def test_user_without_role_gets_403(self):
        self.client.force_login(make_user("nobody"))
        self.assertEqual(self.client.get(reverse("dashboard")).status_code, 403)

    def test_home_redirects_logged_in_user(self):
        self.client.force_login(make_user("p", APPLICANT))
        self.assertRedirects(
            self.client.get(reverse("home")), reverse("dashboard"), fetch_redirect_response=False
        )

    def test_home_anonymous(self):
        resp = self.client.get(reverse("home"))
        self.assertContains(resp, reverse("register"))

    def test_logout_requires_post(self):
        self.client.force_login(make_user("p", APPLICANT))
        resp = self.client.post(reverse("logout"))
        self.assertRedirects(resp, reverse("home"), fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)
