"""切片 4：實驗人員工作台（排程清單、開始、結果輸入與暫存、完成）的測試。"""
from datetime import timedelta

from django.contrib.auth.models import User
from django.urls import reverse

from accounts.roles import LAB_STAFF

from . import services
from .models import Request, RequestItem, Schedule
from .test_admin import AdminFixture
from .test_requests import make_user
from .tests import seed

S = Request.Status


class LabFixture(AdminFixture):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.other_staff = make_user("tech3", LAB_STAFF)

    def started_request(self, **kwargs):
        return services.start(self.scheduled_request(**kwargs), self.staff)

    def fill_all(self, req, value="1.0"):
        return services.save_results(req, self.staff, {i.pk: (value, "") for i in req.items.all()})

    def completed_request(self, **kwargs):
        req = self.started_request(**kwargs)
        self.fill_all(req)
        return services.finish(req, self.staff)


class LabServiceTests(LabFixture):
    def test_start_sets_started_at_and_logs(self):
        req = self.started_request()
        self.assertEqual(req.status, S.IN_PROGRESS)
        self.assertIsNotNone(Schedule.objects.get(request=req).started_at)
        log = req.status_logs.last()
        self.assertEqual((log.from_status, log.to_status, log.actor), (S.SCHEDULED, S.IN_PROGRESS, self.staff))

    def test_save_results_keeps_status_without_log(self):
        req = self.started_request()
        item = req.items.first()
        logs_before = req.status_logs.count()
        services.save_results(req, self.staff, {item.pk: ("  ND ", " 低於定量極限 ")})
        item.refresh_from_db()
        req.refresh_from_db()
        self.assertEqual((item.result_value, item.result_note), ("ND", "低於定量極限"))
        self.assertEqual((req.status, req.status_logs.count()), (S.IN_PROGRESS, logs_before))

    def test_finish_sets_completed_at_and_logs(self):
        req = self.completed_request()
        self.assertEqual(req.status, S.COMPLETED)
        self.assertIsNotNone(Schedule.objects.get(request=req).completed_at)
        self.assertEqual(self.logs(req)[-2:], [(S.SCHEDULED, S.IN_PROGRESS), (S.IN_PROGRESS, S.COMPLETED)])

    def test_finish_rejected_with_missing_results(self):
        req = self.started_request()
        self.assert_rejected(req, services.finish, self.staff)
        first = req.items.first()
        services.save_results(req, self.staff, {first.pk: ("ND", "")})
        with self.assertRaisesMessage(services.TransitionError, "尚有 2 個項目"):
            services.finish(req, self.staff)
        self.assertIsNone(Schedule.objects.get(request=req).completed_at)

    def test_whitespace_only_value_counts_as_missing(self):
        req = self.started_request()
        self.fill_all(req, value="   ")
        self.assert_rejected(req, services.finish, self.staff)

    def test_start_wrong_state(self):
        self.assert_rejected(self.new_request(), services.start, self.staff)
        self.assert_rejected(self.submitted_request(), services.start, self.staff)
        self.assert_rejected(self.started_request(name="執行中"), services.start, self.staff)
        self.assert_rejected(self.completed_request(name="已完成"), services.start, self.staff)

    def test_finish_wrong_state(self):
        self.assert_rejected(self.scheduled_request(), services.finish, self.staff)
        self.assert_rejected(self.completed_request(name="已完成"), services.finish, self.staff)

    def test_only_assigned_staff(self):
        scheduled = self.scheduled_request()
        for user in (self.other_staff, self.admin, self.alice):
            self.assert_rejected(scheduled, services.start, user)
        started = self.started_request(name="執行中")
        self.fill_all(started)
        for user in (self.other_staff, self.admin, self.alice):
            self.assert_rejected(started, services.finish, user)
            with self.assertRaises(services.TransitionError):
                services.save_results(started, user, {started.items.first().pk: ("X", "")})
        self.assertEqual(started.items.first().result_value, "1.0")

    def test_save_results_wrong_state(self):
        scheduled = self.scheduled_request()
        with self.assertRaises(services.TransitionError):
            services.save_results(scheduled, self.staff, {scheduled.items.first().pk: ("1", "")})
        completed = self.completed_request(name="已完成")
        with self.assertRaises(services.TransitionError):
            services.save_results(completed, self.staff, {completed.items.first().pk: ("9", "")})
        self.assertEqual(completed.items.first().result_value, "1.0")

    def test_save_results_rejects_other_request_items(self):
        req = self.started_request()
        other = self.new_request(name="別人的")
        foreign = other.items.first()
        with self.assertRaises(services.TransitionError):
            services.save_results(req, self.staff, {foreign.pk: ("X", "")})
        foreign.refresh_from_db()
        self.assertEqual(foreign.result_value, "")


class LabViewTests(LabFixture):
    def url(self, name, sched=None):
        if name == "dashboard":
            return reverse("lab:lab_dashboard")
        return reverse(f"lab:staff_schedule_{name}", args=[sched.pk])

    def setUp(self):
        self.client.force_login(self.staff)

    def post_results(self, req, action, values):
        data = {"action": action}
        for item in req.items.all():
            data[f"value_{item.pk}"] = values.get(item.test_item.name, "")
            data[f"note_{item.pk}"] = ""
        return self.client.post(self.url("results", req.schedule), data)

    def test_playbook_8_6(self):
        """劇本 §8-6：今日區看到 → 開始 → 部分暫存 → 完成被擋 → 填完 → 完成。"""
        req = self.scheduled_request()
        sched = req.schedule
        dash = self.client.get(self.url("dashboard"))
        self.assertEqual([s.pk for s in dash.context["sections"][0][1]], [sched.pk])
        self.assertContains(dash, self.url("detail", sched))

        detail = self.client.get(self.url("detail", sched))
        self.assertContains(detail, self.url("start", sched))
        self.assertRedirects(self.client.post(self.url("start", sched)), self.url("detail", sched))
        req.refresh_from_db()
        self.assertEqual(req.status, S.IN_PROGRESS)

        names = [i.test_item.name for i in req.items.select_related("test_item")]
        resp = self.post_results(req, "save", {names[0]: "ND"})
        self.assertRedirects(resp, self.url("detail", sched))
        self.assertEqual(RequestItem.objects.get(request=req, test_item__name=names[0]).result_value, "ND")

        resp = self.post_results(req, "finish", {names[0]: "ND", names[1]: "<0.01"})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "尚有 1 個項目未填結果值")
        self.assertContains(resp, 'class="row-missing"', count=1)
        req.refresh_from_db()
        self.assertEqual(req.status, S.IN_PROGRESS)
        # 完成被擋時，已填的值仍已暫存
        self.assertEqual(RequestItem.objects.get(request=req, test_item__name=names[1]).result_value, "<0.01")

        resp = self.post_results(req, "finish", {names[0]: "ND", names[1]: "<0.01", names[2]: "0.12"})
        self.assertRedirects(resp, self.url("detail", sched))
        req.refresh_from_db()
        self.assertEqual(req.status, S.COMPLETED)
        dash = self.client.get(self.url("dashboard"))
        self.assertEqual([s.pk for s in dash.context["sections"][3][1]], [sched.pk])
        detail = self.client.get(self.url("detail", sched))
        self.assertNotContains(detail, self.url("results", sched))
        self.assertContains(detail, "0.12")

    def test_dashboard_sections(self):
        today = self.scheduled_request(name="今日")
        upcoming = services.schedule(self.accepted_request(name="明天"), self.admin, self.tomorrow, self.staff)
        overdue = self.scheduled_request(name="逾期")
        Schedule.objects.filter(request=overdue).update(scheduled_date=self.today - timedelta(days=2))
        running = self.started_request(name="執行中")
        done = self.completed_request(name="已完成")
        services.schedule(self.accepted_request(name="別人的"), self.admin, self.today, self.other_staff)

        dash = self.client.get(self.url("dashboard"))
        got = [[s.request.sample_name for s in rows] for _, rows, _ in dash.context["sections"]]
        self.assertEqual(got, [["今日"], ["逾期", "明天"], ["執行中"], ["已完成"]])
        self.assertContains(dash, "已逾期", count=1)
        self.assertNotContains(dash, "別人的")
        for req in (today, upcoming, running, done):
            self.assertContains(dash, req.request_no)

    def test_other_staff_schedule_404(self):
        req = self.started_request()
        sched = req.schedule
        self.client.force_login(self.other_staff)
        self.assertEqual(self.client.get(self.url("detail", sched)).status_code, 404)
        self.assertEqual(self.client.post(self.url("start", sched)).status_code, 404)
        item = req.items.first()
        resp = self.client.post(self.url("results", sched), {"action": "save", f"value_{item.pk}": "X"})
        self.assertEqual(resp.status_code, 404)
        item.refresh_from_db()
        self.assertEqual(item.result_value, "")

    def test_other_roles_403(self):
        sched = self.scheduled_request().schedule
        for user in (self.alice, self.admin):
            self.client.force_login(user)
            for url in (self.url("dashboard"), self.url("detail", sched)):
                self.assertEqual(self.client.get(url).status_code, 403)
            self.assertEqual(self.client.post(self.url("start", sched)).status_code, 403)

    def test_actions_are_post_only(self):
        sched = self.started_request().schedule
        for name in ("start", "results"):
            self.assertEqual(self.client.get(self.url(name, sched)).status_code, 405)

    def test_illegal_start_shows_error(self):
        sched = self.started_request().schedule
        resp = self.client.post(self.url("start", sched), follow=True)
        self.assertContains(resp, "無法開始實驗")

    def test_results_too_long_rerenders(self):
        req = self.started_request()
        item = req.items.first()
        resp = self.client.post(
            self.url("results", req.schedule), {"action": "save", f"value_{item.pk}": "9" * 33}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context["result_form"].errors)
        item.refresh_from_db()
        self.assertEqual(item.result_value, "")

    def test_applicant_cannot_see_results_before_publish(self):
        running = self.started_request()
        services.save_results(running, self.staff, {running.items.first().pk: ("SECRET-1", "")})
        done = self.completed_request(name="已完成")
        RequestItem.objects.filter(request=done).update(result_value="SECRET-2")
        self.client.force_login(self.alice)
        for req in (running, done):
            resp = self.client.get(reverse("lab:applicant_request_detail", args=[req.pk]))
            self.assertNotContains(resp, "SECRET")


class SeedLabTests(LabFixture):
    def test_seed_covers_lab_states(self):
        seed()
        seed()
        lab1 = User.objects.get(username="lab1")
        statuses = set(Request.objects.values_list("status", flat=True))
        self.assertTrue({S.SCHEDULED, S.IN_PROGRESS, S.COMPLETED} <= statuses)
        self.assertTrue(Schedule.objects.filter(assigned_staff__username="lab2").exists())
        done = Request.objects.get(sample_name="鮮乳")
        self.assertFalse(done.items.filter(result_value="").exists())
        self.client.force_login(lab1)
        sections = self.client.get(reverse("lab:lab_dashboard")).context["sections"]
        self.assertTrue(all(rows for _, rows, _ in sections))
