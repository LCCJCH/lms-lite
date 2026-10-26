"""切片 2：申請單、狀態轉換服務、申請者畫面的測試。"""
import re
from datetime import datetime
from unittest import mock
from zoneinfo import ZoneInfo

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse

from accounts.roles import ADMIN, APPLICANT, LAB_STAFF

from . import services
from .admin import RequestAdmin
from .models import Request, RequestItem, RequestStatusLog, TestItem
from .services import TransitionError
from .tests import seed

S = Request.Status
TPE = ZoneInfo("Asia/Taipei")


def make_user(username, role):
    user = User.objects.create_user(username, password="x", first_name=username)
    user.groups.add(Group.objects.get_or_create(name=role)[0])
    return user


def at(year, month=6, day=1):
    """固定 timezone.now()，用於驗證年度配號。"""
    return mock.patch(
        "django.utils.timezone.now", return_value=datetime(year, month, day, 0, 30, tzinfo=TPE)
    )


class Fixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.alice = make_user("alice", APPLICANT)
        cls.bob = make_user("bob", APPLICANT)
        cls.admin = make_user("boss", ADMIN)
        cls.staff = make_user("tech", LAB_STAFF)
        cls.items = [
            TestItem.objects.create(name=f"項目{i}", unit="mg/kg") for i in range(1, 5)
        ]
        cls.inactive = TestItem.objects.create(name="停用項目", unit="%", is_active=False)

    def new_request(self, user=None, items=None, name="樣品"):
        user = user or self.alice
        items = self.items[:3] if items is None else items
        return services.create_request(user, {"sample_name": name}, items)

    def submitted_request(self, **kwargs):
        req = self.new_request(**kwargs)
        return services.submit(req, req.applicant)

    def logs(self, req):
        return list(req.status_logs.values_list("from_status", "to_status"))


class CreateUpdateDeleteServiceTests(Fixture):
    def test_create_writes_initial_log(self):
        req = self.new_request()
        self.assertEqual(req.status, S.DRAFT)
        self.assertIsNone(req.request_no)
        self.assertEqual(req.items.count(), 3)
        self.assertEqual(self.logs(req), [("", S.DRAFT)])
        self.assertEqual(req.status_logs.get().actor, self.alice)

    def test_only_applicant_can_create(self):
        for user in (self.admin, self.staff):
            with self.assertRaises(TransitionError):
                self.new_request(user=user)
        self.assertFalse(Request.objects.exists())

    def test_update_syncs_items_without_log(self):
        req = self.new_request()
        services.update_request(req, self.alice, {"sample_name": "新名稱"}, self.items[2:4])
        req.refresh_from_db()
        self.assertEqual(req.sample_name, "新名稱")
        self.assertEqual(
            set(req.items.values_list("test_item_id", flat=True)), {self.items[2].pk, self.items[3].pk}
        )
        self.assertEqual(req.status_logs.count(), 1)

    def test_update_allowed_when_returned(self):
        req = self.submitted_request()
        services.return_for_revision(req, self.admin, "請補資料")
        services.update_request(req, self.alice, {"sample_name": "補件"}, self.items[:1])
        self.assertEqual(req.sample_name, "補件")
        self.assertEqual(req.status, S.RETURNED)

    def test_update_rejected_when_submitted_or_not_owner(self):
        req = self.new_request()
        with self.assertRaises(TransitionError):
            services.update_request(req, self.bob, {"sample_name": "x"}, [])
        services.submit(req, self.alice)
        with self.assertRaises(TransitionError):
            services.update_request(req, self.alice, {"sample_name": "x"}, [])
        req.refresh_from_db()
        self.assertEqual(req.sample_name, "樣品")
        self.assertEqual(req.items.count(), 3)

    def test_delete_never_submitted_draft(self):
        req = self.new_request()
        services.delete_request(req, self.alice)
        self.assertFalse(Request.objects.exists())
        self.assertFalse(RequestStatusLog.objects.exists())

    def test_cannot_delete_other_or_submitted_or_withdrawn(self):
        req = self.new_request()
        with self.assertRaises(TransitionError):
            services.delete_request(req, self.bob)
        services.submit(req, self.alice)
        with self.assertRaises(TransitionError):
            services.delete_request(req, self.alice)
        services.withdraw(req, self.alice)
        self.assertEqual(req.status, S.DRAFT)
        with self.assertRaises(TransitionError):
            services.delete_request(req, self.alice)
        self.assertTrue(Request.objects.filter(pk=req.pk).exists())


class SubmitAndNumberingTests(Fixture):
    def test_submit_assigns_number_and_logs(self):
        with at(2026):
            req = self.submitted_request()
        self.assertEqual(req.status, S.SUBMITTED)
        self.assertEqual(req.request_no, "REQ-2026-0001")
        self.assertIsNotNone(req.submitted_at)
        self.assertEqual(self.logs(req), [("", S.DRAFT), (S.DRAFT, S.SUBMITTED)])
        self.assertEqual(req.status_logs.last().actor, self.alice)

    def test_sequential_numbers(self):
        with at(2026):
            nos = [self.submitted_request(name=f"s{i}").request_no for i in range(3)]
        self.assertEqual(nos, ["REQ-2026-0001", "REQ-2026-0002", "REQ-2026-0003"])

    def test_new_year_restarts_at_0001(self):
        with at(2026, 12, 31):
            self.submitted_request()
        with at(2027, 1, 1):
            self.assertEqual(self.submitted_request().request_no, "REQ-2027-0001")

    def test_number_grows_past_9999(self):
        Request.objects.create(applicant=self.bob, sample_name="舊", request_no="REQ-2026-9999")
        with at(2026):
            self.assertEqual(self.submitted_request().request_no, "REQ-2026-10000")

    def test_requires_at_least_one_item(self):
        req = self.new_request(items=[])
        with self.assertRaisesMessage(TransitionError, "至少選擇一個檢測項目"):
            services.submit(req, self.alice)
        req.refresh_from_db()
        self.assertEqual(req.status, S.DRAFT)
        self.assertIsNone(req.request_no)
        self.assertEqual(req.status_logs.count(), 1)

    def test_withdraw_keeps_number_and_resubmit_reuses_it(self):
        req = self.submitted_request()
        no = req.request_no
        services.withdraw(req, self.alice)
        self.assertEqual((req.status, req.request_no), (S.DRAFT, no))
        services.submit(req, self.alice)
        self.assertEqual(req.request_no, no)
        self.assertEqual(
            self.logs(req),
            [("", S.DRAFT), (S.DRAFT, S.SUBMITTED), (S.SUBMITTED, S.DRAFT), (S.DRAFT, S.SUBMITTED)],
        )

    def test_return_and_resubmit(self):
        req = self.submitted_request()
        no = req.request_no
        services.return_for_revision(req, self.admin, "  樣品量不足  ")
        self.assertEqual(req.status, S.RETURNED)
        self.assertEqual(req.return_reason, "樣品量不足")
        log = req.status_logs.last()
        self.assertEqual((log.from_status, log.to_status, log.actor, log.comment),
                         (S.SUBMITTED, S.RETURNED, self.admin, "樣品量不足"))
        services.submit(req, self.alice)
        self.assertEqual((req.status, req.request_no), (S.SUBMITTED, no))
        self.assertEqual(req.return_reason, "樣品量不足")  # 保留最後一次退回理由


class IllegalTransitionTests(Fixture):
    def assert_rejected(self, req, func, *args):
        before = (req.status, req.status_logs.count())
        with self.assertRaises(TransitionError):
            func(req, *args)
        req.refresh_from_db()
        self.assertEqual((req.status, req.status_logs.count()), before)

    def test_wrong_state(self):
        draft = self.new_request()
        self.assert_rejected(draft, services.withdraw, self.alice)
        self.assert_rejected(draft, services.return_for_revision, self.admin, "x")
        submitted = self.submitted_request()
        self.assert_rejected(submitted, services.submit, self.alice)

    def test_wrong_actor(self):
        draft = self.new_request()
        for user in (self.bob, self.admin, self.staff):
            self.assert_rejected(draft, services.submit, user)
        submitted = self.submitted_request()
        for user in (self.bob, self.admin, self.staff):
            self.assert_rejected(submitted, services.withdraw, user)
        for user in (self.alice, self.staff):
            self.assert_rejected(submitted, services.return_for_revision, user, "x")

    def test_return_requires_reason(self):
        submitted = self.submitted_request()
        self.assert_rejected(submitted, services.return_for_revision, self.admin, "   ")

    def test_transition_table_matches_brief(self):
        self.assertEqual(
            {a: (set(f), t) for a, (f, t, _) in services.TRANSITIONS.items()},
            {
                "submit": ({S.DRAFT, S.RETURNED}, S.SUBMITTED),
                "withdraw": ({S.SUBMITTED}, S.DRAFT),
                "return_for_revision": ({S.SUBMITTED}, S.RETURNED),
                "accept": ({S.SUBMITTED}, S.ACCEPTED),
                "schedule": ({S.ACCEPTED}, S.SCHEDULED),
                "start": ({S.SCHEDULED}, S.IN_PROGRESS),
                "finish": ({S.IN_PROGRESS}, S.COMPLETED),
                "publish": ({S.COMPLETED}, S.RESULT_AVAILABLE),
            },
        )

    def test_status_readonly_in_django_admin(self):
        for field in ("status", "request_no", "submitted_at", "published_at"):
            self.assertIn(field, RequestAdmin.readonly_fields)


class ApplicantViewTests(Fixture):
    def url(self, name, req=None):
        return reverse(f"lab:applicant_request_{name}", args=[req.pk] if req else [])

    def test_playbook_8_2(self):
        """劇本 §8-2：新增勾三項存草稿 → 修改 → 送出取得單號。"""
        self.client.force_login(self.alice)
        self.assertEqual(self.client.get(self.url("new")).status_code, 200)
        resp = self.client.post(self.url("new"), {
            "sample_name": "茶葉", "sample_description": "200 g",
            "test_items": [i.pk for i in self.items[:3]], "action": "save",
        })
        req = Request.objects.get()
        self.assertRedirects(resp, self.url("detail", req))
        self.assertEqual((req.status, req.items.count(), req.request_no), (S.DRAFT, 3, None))

        self.assertEqual(self.client.get(self.url("edit", req)).status_code, 200)
        resp = self.client.post(self.url("edit", req), {
            "sample_name": "茶葉（修正）", "test_items": [i.pk for i in self.items[1:4]], "action": "save",
        })
        self.assertRedirects(resp, self.url("detail", req))
        req.refresh_from_db()
        self.assertEqual(req.sample_name, "茶葉（修正）")
        self.assertEqual(req.status, S.DRAFT)

        resp = self.client.post(self.url("edit", req), {
            "sample_name": "茶葉（修正）", "test_items": [i.pk for i in self.items[1:4]], "action": "submit",
        }, follow=True)
        req.refresh_from_db()
        self.assertEqual(req.status, S.SUBMITTED)
        self.assertRegex(req.request_no, r"^REQ-\d{4}-\d{4}$")
        self.assertContains(resp, req.request_no)

        dash = self.client.get(reverse("lab:applicant_dashboard"))
        self.assertContains(dash, req.request_no)

    def test_submit_from_new_form_without_items_rolls_back(self):
        self.client.force_login(self.alice)
        resp = self.client.post(self.url("new"), {"sample_name": "空", "action": "submit"})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "至少選擇一個檢測項目")
        self.assertFalse(Request.objects.exists())

    def test_submit_and_withdraw_buttons(self):
        req = self.new_request()
        self.client.force_login(self.alice)
        self.assertRedirects(self.client.post(self.url("submit", req)), self.url("detail", req))
        req.refresh_from_db()
        self.assertEqual(req.status, S.SUBMITTED)
        self.client.post(self.url("withdraw", req))
        req.refresh_from_db()
        self.assertEqual(req.status, S.DRAFT)

    def test_illegal_action_shows_error(self):
        req = self.new_request()
        self.client.force_login(self.alice)
        resp = self.client.post(self.url("withdraw", req), follow=True)
        self.assertContains(resp, "無法撤回")
        req.refresh_from_db()
        self.assertEqual(req.status, S.DRAFT)

    def test_edit_blocked_when_submitted(self):
        req = self.submitted_request()
        self.client.force_login(self.alice)
        self.assertRedirects(self.client.get(self.url("edit", req)), self.url("detail", req))
        resp = self.client.post(self.url("edit", req), {"sample_name": "改", "action": "save"})
        self.assertContains(resp, "無法修改")
        req.refresh_from_db()
        self.assertEqual(req.sample_name, "樣品")

    def test_delete_draft_and_button_visibility(self):
        req = self.new_request()
        self.client.force_login(self.alice)
        detail = self.client.get(self.url("detail", req))
        self.assertContains(detail, self.url("delete", req))
        self.assertContains(detail, "return confirm('確定刪除這份草稿")
        self.assertRedirects(self.client.post(self.url("delete", req)), reverse("lab:applicant_dashboard"))
        self.assertFalse(Request.objects.exists())

        req = self.submitted_request()
        services.withdraw(req, self.alice)
        self.assertNotContains(self.client.get(self.url("detail", req)), self.url("delete", req))
        self.client.post(self.url("delete", req))
        self.assertTrue(Request.objects.filter(pk=req.pk).exists())

    def test_returned_shows_reason(self):
        req = self.submitted_request()
        services.return_for_revision(req, self.admin, "請補充保存條件")
        self.client.force_login(self.alice)
        self.assertContains(self.client.get(self.url("detail", req)), "請補充保存條件")
        self.assertContains(self.client.get(self.url("edit", req)), "請補充保存條件")

    def test_other_applicants_request_is_404(self):
        req = self.new_request(user=self.bob)
        self.client.force_login(self.alice)
        for name in ("detail", "edit"):
            self.assertEqual(self.client.get(self.url(name, req)).status_code, 404, name)
        for name in ("edit", "submit", "withdraw", "delete"):
            self.assertEqual(self.client.post(self.url(name, req)).status_code, 404, name)
        self.assertTrue(Request.objects.filter(pk=req.pk, status=S.DRAFT).exists())

    def test_other_roles_get_403(self):
        req = self.new_request()
        for user in (self.admin, self.staff):
            self.client.force_login(user)
            self.assertEqual(self.client.get(self.url("new")).status_code, 403)
            self.assertEqual(self.client.get(self.url("detail", req)).status_code, 403)
            self.assertEqual(self.client.post(self.url("submit", req)).status_code, 403)

    def test_anonymous_redirected_to_login(self):
        url = self.url("new")
        self.assertRedirects(self.client.get(url), f"{reverse('login')}?next={url}")

    def test_action_urls_reject_get(self):
        req = self.new_request()
        self.client.force_login(self.alice)
        for name in ("submit", "withdraw", "delete"):
            self.assertEqual(self.client.get(self.url(name, req)).status_code, 405, name)

    def test_inactive_items_hidden_unless_already_selected(self):
        self.client.force_login(self.alice)
        choices = self.client.get(self.url("new")).context["form"].fields["test_items"].queryset
        self.assertNotIn(self.inactive, choices)
        req = self.new_request(items=[self.items[0], self.inactive])
        choices = self.client.get(self.url("edit", req)).context["form"].fields["test_items"].queryset
        self.assertIn(self.inactive, choices)
        self.assertIn(self.items[1], choices)

    def test_cannot_post_inactive_item_on_new(self):
        self.client.force_login(self.alice)
        resp = self.client.post(self.url("new"), {
            "sample_name": "x", "test_items": [self.inactive.pk], "action": "save",
        })
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(Request.objects.exists())

    def test_results_hidden_until_published(self):
        req = self.submitted_request()
        RequestItem.objects.filter(request=req).update(result_value="0.123")
        self.client.force_login(self.alice)
        self.assertNotContains(self.client.get(self.url("detail", req)), "0.123")
        # 測試佈置：直接設定為已發布，驗證顯示邏輯
        Request.objects.filter(pk=req.pk).update(status=S.RESULT_AVAILABLE)
        self.assertContains(self.client.get(self.url("detail", req)), "0.123")

    def test_dashboard_lists_only_own_requests(self):
        self.new_request(name="我的樣品")
        self.new_request(user=self.bob, name="別人的樣品")
        self.client.force_login(self.alice)
        resp = self.client.get(reverse("lab:applicant_dashboard"))
        self.assertContains(resp, "我的樣品")
        self.assertNotContains(resp, "別人的樣品")


class SeedRequestsTests(TestCase):
    def test_seed_creates_demo_requests_idempotently(self):
        seed()
        seed()
        self.assertEqual(Request.objects.count(), 11)
        self.assertEqual(
            sorted(Request.objects.values_list("status", flat=True)),
            sorted([S.DRAFT, S.SUBMITTED, S.SUBMITTED, S.RETURNED, S.ACCEPTED,
                    S.SCHEDULED, S.SCHEDULED, S.SCHEDULED, S.IN_PROGRESS, S.COMPLETED,
                    S.RESULT_AVAILABLE]),
        )
        for req in Request.objects.all():
            self.assertEqual(req.status_logs.first().to_status, S.DRAFT)
            self.assertEqual(req.status_logs.last().to_status, req.status)
            if req.status != S.DRAFT:
                self.assertTrue(re.match(r"^REQ-\d{4}-\d{4}$", req.request_no))
        self.assertTrue(Request.objects.get(status=S.RETURNED).return_reason)
