"""切片 5：Admin 發布、申請者結果頁、歷史查詢，以及完整的 §8-9 權限檢查。"""
import os
from datetime import datetime
from unittest import mock

from django.contrib.auth.models import User
from django.urls import reverse

from . import services
from .models import Request
from .test_lab import LabFixture
from .test_requests import TPE
from .tests import seed

S = Request.Status


def set_submitted_at(req, *args):
    """測試用：把送出時間固定在台北時間的某一刻。"""
    Request.objects.filter(pk=req.pk).update(submitted_at=datetime(*args, tzinfo=TPE))


class PublishServiceTests(LabFixture):
    def test_publish(self):
        req = services.publish(self.completed_request(), self.admin)
        self.assertEqual(req.status, S.RESULT_AVAILABLE)
        self.assertIsNotNone(req.published_at)
        self.assertEqual(self.logs(req)[-1], (S.COMPLETED, S.RESULT_AVAILABLE))

    def test_publish_wrong_state(self):
        for req in (
            self.submitted_request(name="已送出"),
            self.scheduled_request(name="已排程"),
            self.started_request(name="實驗中"),
            services.publish(self.completed_request(name="已發布"), self.admin),
        ):
            self.assert_rejected(req, services.publish, self.admin)
        self.assertEqual(Request.objects.filter(published_at__isnull=False).count(), 1)

    def test_only_admin_can_publish(self):
        req = self.completed_request()
        for user in (self.alice, self.staff):
            self.assert_rejected(req, services.publish, user)
        req.refresh_from_db()
        self.assertIsNone(req.published_at)

    def test_results_read_only_after_publish(self):
        req = services.publish(self.completed_request(), self.admin)
        item = req.items.first()
        self.assert_rejected(req, services.save_results, self.staff, {item.pk: ("9.9", "")})
        self.assert_rejected(req, services.finish, self.staff)
        item.refresh_from_db()
        self.assertEqual(item.result_value, "1.0")


class PublishViewTests(LabFixture):
    def url(self, name, req):
        return reverse(f"lab:admin_request_{name}", args=[req.pk])

    def setUp(self):
        self.client.force_login(self.admin)

    def test_playbook_8_7_8_8(self):
        """劇本 §8-7～8：Admin 檢視結果 → 發布 → 申請者主頁、詳細頁、歷史查詢。"""
        req = self.completed_request(name="綠茶粉")  # 結果值皆為 1.0
        detail = self.client.get(self.url("detail", req))
        self.assertContains(detail, self.url("publish", req))
        self.assertContains(detail, "1.0")

        resp = self.client.post(self.url("publish", req), follow=True)
        self.assertContains(resp, f"已發布 {req.request_no} 的結果")
        self.assertNotContains(resp, self.url("publish", req))
        req.refresh_from_db()
        self.assertEqual(req.status, S.RESULT_AVAILABLE)

        self.client.force_login(self.alice)
        dash = self.client.get(reverse("lab:applicant_dashboard"))
        self.assertEqual(list(dash.context["published_requests"]), [req])
        self.assertNotIn(req, dash.context["active_requests"])
        detail = self.client.get(reverse("lab:applicant_request_detail", args=[req.pk]))
        self.assertContains(detail, "檢測結果")
        self.assertContains(detail, "1.0")

        history = self.client.get(reverse("lab:applicant_history"), {"q": req.request_no})
        self.assertEqual(list(history.context["requests"]), [req])
        self.assertContains(history, "查看結果")

    def test_publish_button_only_when_completed(self):
        req = self.started_request()
        self.assertNotContains(self.client.get(self.url("detail", req)), self.url("publish", req))
        resp = self.client.post(self.url("publish", req), follow=True)
        self.assertContains(resp, "無法發布")
        req.refresh_from_db()
        self.assertEqual(req.status, S.IN_PROGRESS)

    def test_publish_asks_for_confirmation(self):
        req = self.completed_request()
        self.assertContains(self.client.get(self.url("detail", req)), "return confirm('確定發布")

    def test_publish_rejects_get(self):
        req = self.completed_request()
        self.assertEqual(self.client.get(self.url("publish", req)).status_code, 405)

    def test_other_roles_cannot_publish(self):
        req = self.completed_request()
        for user in (self.alice, self.staff):
            self.client.force_login(user)
            self.assertEqual(self.client.post(self.url("publish", req)).status_code, 403)
        req.refresh_from_db()
        self.assertEqual(req.status, S.COMPLETED)


class HistoryViewTests(LabFixture):
    url = reverse("lab:applicant_history")

    def setUp(self):
        self.client.force_login(self.alice)

    def found(self, params=None):
        return list(self.client.get(self.url, params or {}).context["requests"])

    def test_lists_only_own_requests_with_unsubmitted_drafts_last(self):
        draft = self.new_request(name="草稿")
        first = self.submitted_request(name="糙米")
        second = self.submitted_request(name="蜂蜜")
        self.submitted_request(user=self.bob, name="別人的樣品")
        set_submitted_at(first, 2026, 10, 1, 9)
        set_submitted_at(second, 2026, 10, 5, 9)
        self.assertEqual(self.found(), [second, first, draft])
        self.assertNotContains(self.client.get(self.url), "別人的樣品")

    def test_filters(self):
        draft = self.new_request(name="草稿")
        rice = self.submitted_request(name="糙米")
        honey = services.publish(self.completed_request(name="蜂蜜"), self.admin)
        set_submitted_at(rice, 2026, 10, 1, 23, 30)  # 台北 10/1 深夜（UTC 仍是 10/1 15:30）
        set_submitted_at(honey, 2026, 10, 2, 0, 30)  # 台北 10/2 凌晨（UTC 為 10/1 16:30）
        cases = [
            ({"status": S.DRAFT}, [draft]),
            ({"status": S.RESULT_AVAILABLE}, [honey]),
            ({"q": "蜂"}, [honey]),
            ({"q": rice.request_no}, [rice]),
            ({"submitted_from": "2026-10-02"}, [honey]),
            ({"submitted_to": "2026-10-01"}, [rice]),
            ({"submitted_from": "2026-10-01", "submitted_to": "2026-10-02"}, [honey, rice]),
            ({"submitted_from": "2026-10-03"}, []),
        ]
        for params, expected in cases:
            self.assertEqual(self.found(params), expected, params)

    def test_reversed_date_range_shows_error(self):
        self.submitted_request()
        resp = self.client.get(self.url, {"submitted_from": "2026-10-05", "submitted_to": "2026-10-01"})
        self.assertContains(resp, "起日不可晚於迄日")
        html = resp.content.decode()
        # 錯誤訊息以訊息框顯示在篩選表單上方，不在欄位之間
        self.assertIn('flash-msg flash-error">送出日期的起日不可晚於迄日。', html)
        self.assertLess(html.index("起日不可晚於迄日"), html.index('<form method="get"'))

    def test_unpublished_results_hidden(self):
        req = self.completed_request()
        req.items.update(result_value="SECRET")
        self.assertNotContains(self.client.get(self.url), "SECRET")

    def test_other_roles_403_and_anonymous_redirect(self):
        for user in (self.admin, self.staff):
            self.client.force_login(user)
            self.assertEqual(self.client.get(self.url).status_code, 403)
        self.client.logout()
        self.assertRedirects(self.client.get(self.url), f"{reverse('login')}?next={self.url}")

    def test_nav_and_dashboard_link(self):
        resp = self.client.get(reverse("lab:applicant_dashboard"))
        self.assertContains(resp, f'href="{self.url}"', count=2)


class Playbook89Tests(LabFixture):
    """劇本 §8-9 依序走一次；各細項另有個別測試。"""

    @mock.patch.dict(os.environ, {"DEMO_INVITE_CODE": "right-code"})
    def test_playbook_8_9(self):
        bobs = self.submitted_request(user=self.bob, name="別人的樣品")
        others_sched = self.scheduled_request(name="指派給別人").schedule

        # 申請者以網址直接存取別人的申請單 → 404
        self.client.force_login(self.alice)
        self.assertEqual(
            self.client.get(reverse("lab:applicant_request_detail", args=[bobs.pk])).status_code, 404
        )

        # 實驗人員存取未指派給自己的排程 → 404
        self.client.force_login(self.other_staff)
        self.assertEqual(
            self.client.get(reverse("lab:staff_schedule_detail", args=[others_sched.pk])).status_code, 404
        )

        # 申請者無法進入 /admin/ 與其他角色的工作台
        self.client.force_login(self.alice)
        resp = self.client.get(reverse("admin:index"))
        self.assertRedirects(resp, f"{reverse('admin:login')}?next={reverse('admin:index')}")
        for name in ("lab:admin_dashboard", "lab:lab_dashboard"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 403, name)

        # 註冊時填錯邀請碼無法建立帳號
        self.client.logout()
        resp = self.client.post(reverse("register"), {
            "username": "intruder",
            "email": "intruder@example.com",
            "first_name": "闖入者",
            "password1": "Sample-pass-2026",
            "password2": "Sample-pass-2026",
            "invite_code": "wrong-code",
        })
        self.assertContains(resp, "邀請碼不正確")
        self.assertFalse(User.objects.filter(username="intruder").exists())


class SeedResultsTests(LabFixture):
    def test_seed_covers_all_states(self):
        seed()
        seed()
        self.assertEqual(set(Request.objects.values_list("status", flat=True)), set(S.values))
        published = Request.objects.get(status=S.RESULT_AVAILABLE)
        self.assertIsNotNone(published.published_at)
        self.assertFalse(published.items.filter(result_value="").exists())
        self.client.force_login(published.applicant)
        dash = self.client.get(reverse("lab:applicant_dashboard"))
        self.assertIn(published, dash.context["published_requests"])
