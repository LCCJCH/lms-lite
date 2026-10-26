"""切片 3：Admin 工作台（總覽、清單、受理、退回、排程、改排程）的測試。"""
from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

from accounts.roles import LAB_STAFF

from . import services
from .models import Request, Schedule
from .test_requests import Fixture, make_user

S = Request.Status


class AdminFixture(Fixture):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.staff2 = make_user("tech2", LAB_STAFF)
        cls.today = timezone.localdate()
        cls.tomorrow = cls.today + timedelta(days=1)

    def accepted_request(self, **kwargs):
        return services.accept(self.submitted_request(**kwargs), self.admin)

    def scheduled_request(self, **kwargs):
        return services.schedule(self.accepted_request(**kwargs), self.admin, self.today, self.staff)

    def assert_rejected(self, req, func, *args):
        before = (req.status, req.status_logs.count())
        with self.assertRaises(services.TransitionError):
            func(req, *args)
        req.refresh_from_db()
        self.assertEqual((req.status, req.status_logs.count()), before)


class AdminServiceTests(AdminFixture):
    def test_accept(self):
        req = self.accepted_request()
        self.assertEqual(req.status, S.ACCEPTED)
        self.assertEqual(self.logs(req)[-1], (S.SUBMITTED, S.ACCEPTED))

    def test_schedule_creates_schedule_and_log(self):
        req = self.scheduled_request()
        self.assertEqual(req.status, S.SCHEDULED)
        sched = Schedule.objects.get(request=req)
        self.assertEqual(
            (sched.scheduled_date, sched.assigned_staff, sched.scheduled_by, sched.started_at),
            (self.today, self.staff, self.admin, None),
        )
        log = req.status_logs.last()
        self.assertEqual((log.from_status, log.to_status), (S.ACCEPTED, S.SCHEDULED))
        self.assertIn("tech", log.comment)

    def test_accept_and_schedule_wrong_state(self):
        draft = self.new_request()
        self.assert_rejected(draft, services.accept, self.admin)
        submitted = self.submitted_request()
        self.assert_rejected(submitted, services.schedule, self.admin, self.today, self.staff)
        scheduled = self.scheduled_request()
        self.assert_rejected(scheduled, services.accept, self.admin)
        self.assert_rejected(scheduled, services.return_for_revision, self.admin, "x")
        self.assertEqual(Schedule.objects.count(), 1)

    def test_non_admin_rejected(self):
        submitted = self.submitted_request()
        for user in (self.alice, self.bob, self.staff):
            self.assert_rejected(submitted, services.accept, user)
        accepted = self.accepted_request()
        for user in (self.alice, self.staff):
            self.assert_rejected(accepted, services.schedule, user, self.today, self.staff)
        self.assertFalse(Schedule.objects.exists())

    def test_assigned_staff_must_be_active_lab_staff(self):
        req = self.accepted_request()
        for user in (self.alice, self.admin):
            self.assert_rejected(req, services.schedule, self.admin, self.today, user)
        self.staff2.is_active = False
        self.staff2.save()
        self.assert_rejected(req, services.schedule, self.admin, self.today, self.staff2)
        self.assertFalse(Schedule.objects.exists())

    def test_staff_who_is_also_admin_can_be_assigned(self):
        both = make_user("both", LAB_STAFF)
        both.groups.add(self.admin.groups.get())
        req = services.schedule(self.accepted_request(), self.admin, self.today, both)
        self.assertEqual(req.schedule.assigned_staff, both)

    def test_reschedule_logs_change_and_keeps_status(self):
        req = self.scheduled_request()
        services.reschedule(req, self.admin, self.tomorrow, self.staff2)
        req.refresh_from_db()
        sched = Schedule.objects.get(request=req)
        self.assertEqual(req.status, S.SCHEDULED)
        self.assertEqual((sched.scheduled_date, sched.assigned_staff), (self.tomorrow, self.staff2))
        log = req.status_logs.last()
        self.assertEqual((log.from_status, log.to_status), (S.SCHEDULED, S.SCHEDULED))
        self.assertIn(f"{self.today:%Y-%m-%d} → {self.tomorrow:%Y-%m-%d}", log.comment)
        self.assertIn("tech → tech2", log.comment)

    def test_reschedule_rejections(self):
        req = self.scheduled_request()
        self.assert_rejected(req, services.reschedule, self.admin, self.today, self.staff)  # 無變更
        self.assert_rejected(req, services.reschedule, self.staff, self.tomorrow, self.staff)
        self.assert_rejected(req, services.reschedule, self.admin, self.tomorrow, self.alice)
        accepted = self.accepted_request(name="另一件")
        self.assert_rejected(accepted, services.reschedule, self.admin, self.tomorrow, self.staff)
        self.assertEqual(Schedule.objects.get(request=req).scheduled_date, self.today)


class AdminViewTests(AdminFixture):
    def url(self, name, req=None):
        return reverse(f"lab:admin_request_{name}", args=[req.pk] if req else [])

    def setUp(self):
        self.client.force_login(self.admin)

    def test_playbook_8_3_to_5(self):
        """劇本 §8-3～5：待受理 → 退回 → 申請者重送 → 受理 → 排程。"""
        req = self.submitted_request()
        dash = self.client.get(reverse("lab:admin_dashboard"))
        self.assertContains(dash, req.request_no)
        self.assertContains(dash, self.url("detail", req))

        resp = self.client.post(self.url("return", req), {"reason": "請補充保存條件"})
        self.assertRedirects(resp, self.url("detail", req))
        req.refresh_from_db()
        self.assertEqual((req.status, req.return_reason), (S.RETURNED, "請補充保存條件"))

        self.client.force_login(self.alice)
        detail = self.client.get(reverse("lab:applicant_request_detail", args=[req.pk]))
        self.assertContains(detail, "請補充保存條件")
        self.client.post(reverse("lab:applicant_request_submit", args=[req.pk]))
        req.refresh_from_db()
        self.assertEqual(req.status, S.SUBMITTED)

        self.client.force_login(self.admin)
        self.client.post(self.url("accept", req))
        req.refresh_from_db()
        self.assertEqual(req.status, S.ACCEPTED)
        resp = self.client.post(
            self.url("schedule", req),
            {"scheduled_date": self.tomorrow.isoformat(), "assigned_staff": self.staff.pk},
        )
        self.assertRedirects(resp, self.url("detail", req))
        req.refresh_from_db()
        self.assertEqual(req.status, S.SCHEDULED)
        self.assertEqual(req.schedule.assigned_staff, self.staff)
        self.assertEqual(
            self.logs(req),
            [("", S.DRAFT), (S.DRAFT, S.SUBMITTED), (S.SUBMITTED, S.RETURNED),
             (S.RETURNED, S.SUBMITTED), (S.SUBMITTED, S.ACCEPTED), (S.ACCEPTED, S.SCHEDULED)],
        )

        # 申請者主頁與詳細頁看得到排程日期
        self.client.force_login(self.alice)
        for url in (reverse("lab:applicant_dashboard"), reverse("lab:applicant_request_detail", args=[req.pk])):
            self.assertContains(self.client.get(url), f"{self.tomorrow:%Y-%m-%d}")

    def test_return_without_reason_shows_error(self):
        req = self.submitted_request()
        resp = self.client.post(self.url("return", req), {"reason": ""})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "這個欄位是必須的")
        req.refresh_from_db()
        self.assertEqual(req.status, S.SUBMITTED)

    def test_schedule_form_rejects_past_date_and_non_staff(self):
        req = self.accepted_request()
        yesterday = self.today - timedelta(days=1)
        resp = self.client.post(
            self.url("schedule", req), {"scheduled_date": yesterday.isoformat(), "assigned_staff": self.staff.pk}
        )
        self.assertContains(resp, "不可早於今天")
        resp = self.client.post(
            self.url("schedule", req), {"scheduled_date": self.today.isoformat(), "assigned_staff": self.alice.pk}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context["schedule_form"].errors.get("assigned_staff"))
        req.refresh_from_db()
        self.assertEqual(req.status, S.ACCEPTED)

    def test_reschedule_view(self):
        req = self.scheduled_request()
        detail = self.client.get(self.url("detail", req))
        self.assertContains(detail, self.url("reschedule", req))
        resp = self.client.post(
            self.url("reschedule", req),
            {"scheduled_date": self.tomorrow.isoformat(), "assigned_staff": self.staff2.pk},
            follow=True,
        )
        self.assertContains(resp, "改排程")
        req.refresh_from_db()
        self.assertEqual(req.schedule.assigned_staff, self.staff2)

    def test_illegal_action_shows_error(self):
        req = self.accepted_request()
        resp = self.client.post(self.url("accept", req), follow=True)
        self.assertContains(resp, "無法受理")

    def test_actions_by_state(self):
        submitted = self.submitted_request()
        accepted = self.accepted_request(name="已受理")
        resp = self.client.get(self.url("detail", submitted))
        self.assertContains(resp, self.url("accept", submitted))
        self.assertContains(resp, self.url("return", submitted))
        self.assertNotContains(resp, self.url("schedule", submitted))
        resp = self.client.get(self.url("detail", accepted))
        self.assertContains(resp, self.url("schedule", accepted))
        self.assertNotContains(resp, self.url("accept", accepted))

    def test_drafts_hidden_from_admin(self):
        draft = self.new_request(name="草稿樣品")
        withdrawn = services.withdraw(self.submitted_request(name="撤回樣品"), self.alice)
        self.assertEqual(self.client.get(self.url("detail", draft)).status_code, 404)
        self.assertEqual(self.client.get(self.url("detail", withdrawn)).status_code, 404)
        self.assertEqual(self.client.post(self.url("accept", draft)).status_code, 404)
        resp = self.client.get(self.url("list"))
        self.assertNotContains(resp, "草稿樣品")
        self.assertNotContains(resp, "撤回樣品")

    def test_dashboard_counts(self):
        self.new_request()
        self.submitted_request()
        self.submitted_request()
        self.scheduled_request()
        stats = {s["status"]: s["count"] for s in self.client.get(reverse("lab:admin_dashboard")).context["stats"]}
        self.assertNotIn(S.DRAFT, stats)
        self.assertEqual((stats[S.SUBMITTED], stats[S.SCHEDULED], stats[S.ACCEPTED]), (2, 1, 0))

    def test_list_filters(self):
        a = self.submitted_request(name="糙米")
        b = self.accepted_request(user=self.bob, name="蜂蜜")
        cases = [
            ({"status": S.ACCEPTED}, [b]),
            ({"applicant": self.alice.pk}, [a]),
            ({"q": "蜂"}, [b]),
            ({"q": a.request_no}, [a]),
            ({}, [b, a]),
        ]
        for params, expected in cases:
            resp = self.client.get(self.url("list"), params)
            self.assertEqual(list(resp.context["requests"]), expected, params)

    def test_action_urls_reject_get(self):
        req = self.submitted_request()
        for name in ("accept", "return", "schedule", "reschedule"):
            self.assertEqual(self.client.get(self.url(name, req)).status_code, 405)

    def test_other_roles_get_403(self):
        req = self.submitted_request()
        urls = [reverse("lab:admin_dashboard"), self.url("list"), self.url("detail", req)]
        posts = [self.url(n, req) for n in ("accept", "return", "schedule", "reschedule")]
        for user in (self.alice, self.staff):
            self.client.force_login(user)
            for url in urls:
                self.assertEqual(self.client.get(url).status_code, 403, url)
            for url in posts:
                self.assertEqual(self.client.post(url, {"reason": "x"}).status_code, 403, url)
        req.refresh_from_db()
        self.assertEqual(req.status, S.SUBMITTED)

    def test_applicant_still_cannot_see_results_when_scheduled(self):
        req = self.scheduled_request()
        req.items.update(result_value="0.12")
        self.client.force_login(self.alice)
        resp = self.client.get(reverse("lab:applicant_request_detail", args=[req.pk]))
        self.assertNotContains(resp, "0.12")
