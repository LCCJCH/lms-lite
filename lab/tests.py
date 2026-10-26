import os
import re
from decimal import Decimal, InvalidOperation
from io import StringIO
from unittest import mock

from django.contrib.auth.models import Group, User
from django.core.management import CommandError, call_command
from django.test import TestCase
from django.urls import reverse

from accounts.roles import ADMIN, APPLICANT, LAB_STAFF

from . import services
from .models import Request, RequestItem, TestItem

SEED_PW = "seed-pass-for-tests"
DASHBOARDS = {
    ADMIN: "lab:admin_dashboard",
    LAB_STAFF: "lab:lab_dashboard",
    APPLICANT: "lab:applicant_dashboard",
}


def seed():
    with mock.patch.dict(os.environ, {"DEMO_SEED_PASSWORD": SEED_PW}):
        call_command("seed_demo", stdout=StringIO())


class SeedDemoTests(TestCase):
    def test_creates_groups_users_items(self):
        seed()
        self.assertEqual(
            set(Group.objects.values_list("name", flat=True)), {ADMIN, LAB_STAFF, APPLICANT}
        )
        self.assertEqual(User.objects.count(), 5)
        self.assertEqual(TestItem.objects.count(), 47)
        admin = User.objects.get(username="demo_admin")
        self.assertTrue(admin.is_staff)
        self.assertFalse(admin.is_superuser)
        self.assertTrue(admin.check_password(SEED_PW))
        self.assertFalse(User.objects.get(username="lab1").is_staff)

    def test_idempotent(self):
        seed()
        seed()
        self.assertEqual(Group.objects.count(), 3)
        self.assertEqual(User.objects.count(), 5)
        self.assertEqual(TestItem.objects.count(), 47)
        self.assertEqual(User.objects.get(username="lab1").groups.count(), 1)

    def test_admin_group_has_exactly_twelve_perms(self):
        seed()
        codenames = set(
            Group.objects.get(name=ADMIN).permissions.values_list(
                "content_type__app_label", "codename"
            )
        )
        expected = {
            (app, f"{action}_{model}")
            for app, model in [("auth", "user"), ("auth", "group"), ("lab", "testitem")]
            for action in ("view", "add", "change", "delete")
        }
        self.assertEqual(codenames, expected)
        self.assertFalse(Group.objects.get(name=LAB_STAFF).permissions.exists())
        self.assertFalse(Group.objects.get(name=APPLICANT).permissions.exists())

    def test_requires_password_env(self):
        with mock.patch.dict(os.environ, {"DEMO_SEED_PASSWORD": ""}):
            with self.assertRaises(CommandError):
                call_command("seed_demo", stdout=StringIO())
        self.assertFalse(User.objects.exists())


class SeedDemoRequestTests(TestCase):
    def test_sample_names_plain_and_note_explains_state(self):
        seed()
        reqs = list(Request.objects.all())
        self.assertEqual(len(reqs), 11)
        self.assertEqual(len({r.sample_name for r in reqs}), 11)
        for req in reqs:
            self.assertNotIn("示範", req.sample_name)
            self.assertTrue(req.note.startswith("示範資料：初始狀態為"), req.note)


class SeedTestItemTests(TestCase):
    """切片 6.1：分類、定量極限與示範結果值。"""

    @classmethod
    def setUpTestData(cls):
        seed()

    def test_all_four_categories_seeded_in_display_order(self):
        counts = {c: TestItem.objects.filter(category=c).count() for c in TestItem.Category.values}
        self.assertEqual(counts, {"organic": 20, "inorganic": 22, "physchem": 2, "other": 3})
        ordered = list(TestItem.objects.values_list("category", flat=True))
        self.assertEqual(ordered, sorted(ordered, key=TestItem.Category.values.index))
        # 類內依 seed 順序：每類第一項即附件的第一項
        firsts = [TestItem.objects.filter(category=c).first().name for c in TestItem.Category.values]
        self.assertEqual(firsts, ["農藥殘留（多重殘留）", "鉛（Pb）", "水分", "總生菌數"])
        self.assertEqual(TestItem.objects.get(name="鉛（Pb）").loq, "0.02")
        self.assertEqual(TestItem.objects.get(name="水分").loq, "")

    def test_demo_requests_cover_all_categories(self):
        used = set(RequestItem.objects.values_list("test_item__category", flat=True))
        self.assertEqual(used, set(TestItem.Category.values))

    def test_seed_results_are_nd_or_not_below_loq(self):
        """每個非空結果值都必須是 ND 或純數字；有 LOQ 時數值不得低於 LOQ。不跳過任何值。"""
        filled = RequestItem.objects.exclude(result_value="").select_related("test_item", "request")
        self.assertTrue(filled.exists())
        for item in filled:
            value, loq = item.result_value, item.test_item.loq
            with self.subTest(sample=item.request.sample_name, item=item.test_item.name, value=value):
                if value == "ND":
                    continue
                self.assertRegex(value, r"^\d+(\.\d+)?$", "結果值須為 ND 或純數字")
                try:
                    number = Decimal(value)
                except InvalidOperation:
                    self.fail(f"結果值無法以 Decimal 解析：{value}")
                if loq:
                    self.assertGreaterEqual(number, Decimal(loq))

    def test_completed_and_published_requests_have_all_results(self):
        done = RequestItem.objects.filter(
            request__status__in=[Request.Status.COMPLETED, Request.Status.RESULT_AVAILABLE]
        )
        self.assertTrue(done.exists())
        self.assertFalse(done.filter(result_value="").exists())

    def test_request_form_groups_items_by_category(self):
        self.client.force_login(User.objects.get(username="applicant1"))
        html = self.client.get(reverse("lab:applicant_request_new")).content.decode()
        positions = [html.index(f"<summary>{label}（") for label in TestItem.Category.labels]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("<summary>有機（20 項）</summary>", html)
        self.assertIn("定量極限 0.02", html)
        self.assertIn("定量極限 —", html)

    def test_edit_form_opens_groups_with_selected_items(self):
        applicant = User.objects.get(username="applicant1")
        req = Request.objects.get(applicant=applicant, sample_name="醬油")
        self.client.force_login(applicant)
        html = self.client.get(reverse("lab:applicant_request_edit", args=[req.pk])).content.decode()
        opened = re.findall(r'<details class="checkbox-group" open>\s*<summary>(.+?)（', html)
        self.assertEqual(opened, ["有機", "物化性"])

    def assert_loq_column(self, html):
        self.assertIn("<th>定量極限</th>", html)
        self.assertRegex(html, r"<td>鉛（Pb）</td>\s*<td>mg/kg</td>\s*<td>0\.02</td>")
        self.assertRegex(html, r"<td>水分</td>\s*<td>%</td>\s*<td>—</td>")

    def test_result_tables_show_loq(self):
        applicant = User.objects.get(username="applicant1")
        published = Request.objects.get(sample_name="綠茶粉")
        self.client.force_login(applicant)
        self.assert_loq_column(
            self.client.get(reverse("lab:applicant_request_detail", args=[published.pk])).content.decode()
        )
        self.client.force_login(User.objects.get(username="demo_admin"))
        self.assert_loq_column(
            self.client.get(reverse("lab:admin_request_detail", args=[published.pk])).content.decode()
        )
        # 實驗人員：唯讀表（已完成的鮮乳）與結果輸入表（實驗中的雞蛋）
        completed = Request.objects.get(sample_name="鮮乳")
        self.client.force_login(completed.schedule.assigned_staff)
        self.assert_loq_column(
            self.client.get(reverse("lab:staff_schedule_detail", args=[completed.schedule.pk])).content.decode()
        )
        running = Request.objects.get(sample_name="雞蛋")
        self.client.force_login(running.schedule.assigned_staff)
        html = self.client.get(reverse("lab:staff_schedule_detail", args=[running.schedule.pk])).content.decode()
        self.assertIn('name="value_', html)
        self.assertRegex(html, r"<td>總生菌數</td>\s*<td>CFU/g</td>\s*<td>10</td>")


class ResetDemoTests(TestCase):
    def setUp(self):
        seed()
        self.superuser = User.objects.create_superuser("alan", "alan@example.com", "x")
        visitor = User.objects.create_user("visitor", password="x")
        visitor.groups.add(Group.objects.get(name=APPLICANT))
        TestItem.objects.create(name="臨時項目", unit="mg/kg")
        # 訪客的申請單排在示範單之後配號；重置後單號應重新從 0001 起算
        req = services.create_request(visitor, {"sample_name": "訪客樣品"}, [TestItem.objects.get(name="臨時項目")])
        services.submit(req, visitor)

    def reset(self, *args, password=SEED_PW):
        with mock.patch.dict(os.environ, {"DEMO_SEED_PASSWORD": password}):
            call_command("reset_demo", *args, stdout=StringIO())

    def test_requires_yes(self):
        before = (Request.objects.count(), User.objects.count(), TestItem.objects.count())
        with self.assertRaises(CommandError):
            self.reset()
        self.assertEqual(before, (Request.objects.count(), User.objects.count(), TestItem.objects.count()))

    def test_requires_password_and_deletes_nothing(self):
        before = (Request.objects.count(), User.objects.count(), TestItem.objects.count())
        with self.assertRaises(CommandError):
            self.reset("--yes", password="")
        self.assertEqual(before, (Request.objects.count(), User.objects.count(), TestItem.objects.count()))

    def test_reset_restores_fresh_seed_and_keeps_superuser(self):
        self.reset("--yes")
        self.assertTrue(User.objects.filter(pk=self.superuser.pk, is_superuser=True).exists())
        self.assertFalse(User.objects.filter(username="visitor").exists())
        self.assertFalse(TestItem.objects.filter(name="臨時項目").exists())
        self.assertFalse(Request.objects.filter(sample_name="訪客樣品").exists())
        self.assertEqual(User.objects.count(), 6)
        self.assertEqual(TestItem.objects.count(), 47)
        self.assertEqual(Request.objects.count(), 11)
        self.assertEqual(Group.objects.count(), 3)
        numbers = sorted(Request.objects.exclude(request_no=None).values_list("request_no", flat=True))
        self.assertTrue(numbers[0].endswith("-0001"), numbers)


class DashboardAccessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        seed()
        cls.users = {
            ADMIN: User.objects.get(username="demo_admin"),
            LAB_STAFF: User.objects.get(username="lab1"),
            APPLICANT: User.objects.get(username="applicant1"),
        }

    def test_anonymous_redirected_to_login(self):
        for url_name in DASHBOARDS.values():
            url = reverse(url_name)
            self.assertRedirects(self.client.get(url), f"{reverse('login')}?next={url}")

    def test_own_dashboard_ok_other_dashboards_403(self):
        for role, user in self.users.items():
            self.client.force_login(user)
            for target_role, url_name in DASHBOARDS.items():
                with self.subTest(role=role, target=target_role):
                    status = self.client.get(reverse(url_name)).status_code
                    self.assertEqual(status, 200 if role == target_role else 403)

    def test_non_admin_roles_cannot_enter_django_admin(self):
        for role in (LAB_STAFF, APPLICANT):
            self.client.force_login(self.users[role])
            resp = self.client.get(reverse("admin:index"))
            self.assertEqual(resp.status_code, 302, role)
            self.assertIn(reverse("admin:login"), resp["Location"])

    def test_admin_sees_only_user_group_testitem_in_django_admin(self):
        self.client.force_login(self.users[ADMIN])
        resp = self.client.get(reverse("admin:index"))
        self.assertEqual(resp.status_code, 200)
        models = {
            (app["app_label"], m["object_name"])
            for app in resp.context["app_list"]
            for m in app["models"]
        }
        self.assertEqual(models, {("auth", "User"), ("auth", "Group"), ("lab", "TestItem")})

    def test_admin_can_add_lab_staff_in_django_admin(self):
        self.client.force_login(self.users[ADMIN])
        self.assertEqual(self.client.get(reverse("admin:auth_user_add")).status_code, 200)
