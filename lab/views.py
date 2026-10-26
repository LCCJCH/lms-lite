from django.contrib import messages
from django.db import transaction
from django.db.models import Count, F
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views import View
from django.views.generic import FormView, TemplateView

from accounts.mixins import AdminRequiredMixin, ApplicantRequiredMixin, LabStaffRequiredMixin

from . import services
from .forms import (
    AdminRequestFilterForm,
    ApplicantHistoryFilterForm,
    RequestForm,
    ResultForm,
    ReturnForm,
    ScheduleForm,
)
from .models import Request, Schedule
from .services import TransitionError

S = Request.Status


# ---------- 申請者 ----------


class OwnRequestMixin(ApplicantRequiredMixin):
    """只取得自己的申請單；他人的申請單一律 404（簡報 §3）。"""

    def get_own_request(self):
        return get_object_or_404(Request, pk=self.kwargs["pk"], applicant=self.request.user)


def detail_redirect(req):
    return redirect("lab:applicant_request_detail", pk=req.pk)


class ApplicantDashboardView(ApplicantRequiredMixin, TemplateView):
    template_name = "lab/applicant/dashboard.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        own = Request.objects.filter(applicant=self.request.user).select_related("schedule")
        ctx["active_requests"] = own.exclude(status=Request.Status.RESULT_AVAILABLE)
        ctx["published_requests"] = own.filter(status=Request.Status.RESULT_AVAILABLE).order_by(
            "-published_at"
        )[:5]
        return ctx


class ApplicantHistoryView(ApplicantRequiredMixin, TemplateView):
    """歷史申請：只列自己的申請單；未送出的草稿排最後。"""

    template_name = "lab/applicant/history.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        form = ApplicantHistoryFilterForm(self.request.GET or None)
        own = Request.objects.filter(applicant=self.request.user).select_related("schedule")
        ctx["filter_form"] = form
        ctx["requests"] = form.filter(
            own.order_by(F("submitted_at").desc(nulls_last=True), "-created_at", "-id")
        )
        return ctx


class RequestFormViewBase(FormView):
    """新增與修改共用：「儲存草稿」或「送出」；送出失敗時整筆回滾並保留輸入。"""

    form_class = RequestForm
    template_name = "lab/applicant/request_form.html"

    def save(self, form):
        raise NotImplementedError

    def form_valid(self, form):
        do_submit = self.request.POST.get("action") == "submit"
        try:
            with transaction.atomic():
                req = self.save(form)
                if do_submit:
                    services.submit(req, self.request.user)
        except TransitionError as e:
            form.add_error(None, str(e))
            return self.form_invalid(form)
        if do_submit:
            messages.success(self.request, f"已送出，申請單號 {req.request_no}。")
        else:
            messages.success(self.request, "草稿已儲存。")
        return detail_redirect(req)


class RequestCreateView(ApplicantRequiredMixin, RequestFormViewBase):
    def save(self, form):
        return services.create_request(
            self.request.user, form.service_data(), form.cleaned_data["test_items"]
        )


class RequestUpdateView(OwnRequestMixin, RequestFormViewBase):
    def get(self, request, *args, **kwargs):
        self.object = self.get_own_request()
        if not self.object.is_editable:
            messages.error(request, f"申請單目前為「{self.object.get_status_display()}」，無法修改。")
            return detail_redirect(self.object)
        return super().get(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        self.object = self.get_own_request()
        return super().post(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["instance"] = self.object
        return kwargs

    def get_context_data(self, **kwargs):
        return super().get_context_data(req=self.object, **kwargs)

    def save(self, form):
        return services.update_request(
            self.object, self.request.user, form.service_data(), form.cleaned_data["test_items"]
        )


class RequestDetailView(OwnRequestMixin, TemplateView):
    template_name = "lab/applicant/request_detail.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        req = self.get_own_request()
        ctx.update(
            req=req,
            items=req.items.select_related("test_item"),
            logs=req.status_logs.select_related("actor"),
            schedule=getattr(req, "schedule", None),
            # 申請者在結果發布前看不到結果值（簡報 §5）
            show_results=req.status == Request.Status.RESULT_AVAILABLE,
            can_edit=req.is_editable,
            can_withdraw=req.status == Request.Status.SUBMITTED,
            can_delete=req.is_deletable,
        )
        return ctx


class RequestActionView(OwnRequestMixin, View):
    """送出、撤回、刪除：只接受 POST。"""

    http_method_names = ["post"]

    def perform(self, req):
        raise NotImplementedError

    def post(self, request, *args, **kwargs):
        req = self.get_own_request()
        try:
            return self.perform(req)
        except TransitionError as e:
            messages.error(request, str(e))
            return detail_redirect(req)


class RequestSubmitView(RequestActionView):
    def perform(self, req):
        services.submit(req, self.request.user)
        messages.success(self.request, f"已送出，申請單號 {req.request_no}。")
        return detail_redirect(req)


class RequestWithdrawView(RequestActionView):
    def perform(self, req):
        services.withdraw(req, self.request.user)
        messages.success(self.request, "已撤回，申請單回到草稿狀態。")
        return detail_redirect(req)


class RequestDeleteView(RequestActionView):
    def perform(self, req):
        services.delete_request(req, self.request.user)
        messages.success(self.request, "草稿已刪除。")
        return redirect("lab:applicant_dashboard")


# ---------- Admin ----------


def admin_requests():
    """Admin 看得到的申請單：不含草稿（Alan 確認）。"""
    return Request.objects.exclude(status=S.DRAFT).select_related("applicant", "schedule")


def admin_detail_redirect(req):
    return redirect("lab:admin_request_detail", pk=req.pk)


class AdminRequestMixin(AdminRequiredMixin):
    def get_admin_request(self):
        return get_object_or_404(admin_requests(), pk=self.kwargs["pk"])


class AdminDashboardView(AdminRequiredMixin, TemplateView):
    template_name = "lab/admin/dashboard.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        counts = dict(
            admin_requests().order_by().values("status").annotate(n=Count("id")).values_list("status", "n")
        )
        ctx["stats"] = [
            {"status": value, "label": label, "count": counts.get(value, 0)}
            for value, label in S.choices
            if value != S.DRAFT
        ]
        ctx["pending"] = admin_requests().filter(status=S.SUBMITTED).order_by("submitted_at", "id")
        return ctx


class AdminRequestListView(AdminRequiredMixin, TemplateView):
    template_name = "lab/admin/request_list.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        form = AdminRequestFilterForm(self.request.GET or None)
        ctx["filter_form"] = form
        ctx["requests"] = form.filter(admin_requests().order_by("-submitted_at", "-id"))
        return ctx


class AdminRequestDetailView(AdminRequestMixin, TemplateView):
    """詳細頁；動作 view 在表單驗證失敗時也以此頁重新渲染，保留輸入與錯誤。"""

    template_name = "lab/admin/request_detail.html"

    def get_context_data(self, return_form=None, schedule_form=None, **kwargs):
        ctx = super().get_context_data(**kwargs)
        req = self.get_admin_request()
        schedule = getattr(req, "schedule", None)
        if schedule_form is None:
            initial = {}
            if schedule:
                initial = {"scheduled_date": schedule.scheduled_date, "assigned_staff": schedule.assigned_staff}
            schedule_form = ScheduleForm(initial=initial)
        ctx.update(
            req=req,
            items=req.items.select_related("test_item"),
            logs=req.status_logs.select_related("actor"),
            schedule=schedule,
            can_accept=req.status == S.SUBMITTED,
            can_return=req.status == S.SUBMITTED,
            can_schedule=req.status == S.ACCEPTED,
            can_reschedule=req.status == S.SCHEDULED,
            can_publish=req.status == S.COMPLETED,
            return_form=return_form or ReturnForm(),
            schedule_form=schedule_form,
        )
        return ctx


class AdminActionView(AdminRequestDetailView):
    """受理、退回、排程、改排程：只接受 POST。"""

    http_method_names = ["post"]
    form_class = None
    form_context_name = None

    def perform(self, req, form):
        raise NotImplementedError

    def post(self, request, *args, **kwargs):
        req = self.get_admin_request()
        form = self.form_class(request.POST) if self.form_class else None
        if form is not None and not form.is_valid():
            return self.render_to_response(self.get_context_data(**{self.form_context_name: form}))
        try:
            self.perform(req, form)
        except TransitionError as e:
            messages.error(request, str(e))
        return admin_detail_redirect(req)


class AdminAcceptView(AdminActionView):
    def perform(self, req, form):
        services.accept(req, self.request.user)
        messages.success(self.request, f"已受理 {req.request_no}。")


class AdminReturnView(AdminActionView):
    form_class = ReturnForm
    form_context_name = "return_form"

    def perform(self, req, form):
        services.return_for_revision(req, self.request.user, form.cleaned_data["reason"])
        messages.success(self.request, f"已退回 {req.request_no}，等待申請者修改。")


class AdminScheduleView(AdminActionView):
    form_class = ScheduleForm
    form_context_name = "schedule_form"

    def perform(self, req, form):
        services.schedule(
            req, self.request.user, form.cleaned_data["scheduled_date"], form.cleaned_data["assigned_staff"]
        )
        messages.success(self.request, f"已排程 {req.request_no}。")


class AdminRescheduleView(AdminScheduleView):
    def perform(self, req, form):
        services.reschedule(
            req, self.request.user, form.cleaned_data["scheduled_date"], form.cleaned_data["assigned_staff"]
        )
        messages.success(self.request, f"已修改 {req.request_no} 的排程。")


class AdminPublishView(AdminActionView):
    def perform(self, req, form):
        services.publish(req, self.request.user)
        messages.success(self.request, f"已發布 {req.request_no} 的結果。")


# ---------- 實驗人員 ----------


def staff_detail_redirect(sched):
    return redirect("lab:staff_schedule_detail", pk=sched.pk)


class AssignedScheduleMixin(LabStaffRequiredMixin):
    """只取得指派給自己的排程；他人的排程一律 404（簡報 §3）。"""

    def get_assigned_schedule(self):
        return get_object_or_404(
            Schedule.objects.select_related("request", "request__applicant"),
            pk=self.kwargs["pk"],
            assigned_staff=self.request.user,
        )


class LabDashboardView(LabStaffRequiredMixin, TemplateView):
    """我的排程：今日、待執行、執行中、已完成四區。"""

    template_name = "lab/staff/dashboard.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        today = timezone.localdate()
        sections = {"today": [], "upcoming": [], "in_progress": [], "done": []}
        schedules = Schedule.objects.filter(assigned_staff=self.request.user).select_related("request")
        for sched in schedules.order_by("scheduled_date", "id"):
            status = sched.request.status
            if status == S.SCHEDULED:
                sections["today" if sched.scheduled_date == today else "upcoming"].append(sched)
            elif status == S.IN_PROGRESS:
                sections["in_progress"].append(sched)
            else:
                sections["done"].append(sched)
        sections["done"].sort(key=lambda s: s.completed_at or s.updated_at, reverse=True)
        ctx.update(today=today, sections=[
            ("今日", sections["today"], "今天沒有待開始的排程。"),
            ("待執行", sections["upcoming"], "沒有其他待執行的排程。"),
            ("執行中", sections["in_progress"], "沒有執行中的實驗。"),
            ("已完成", sections["done"], "尚無已完成的實驗。"),
        ])
        return ctx


class ScheduleDetailView(AssignedScheduleMixin, TemplateView):
    """排程詳細；結果表單驗證或完成失敗時也以此頁重新渲染，保留輸入。"""

    template_name = "lab/staff/schedule_detail.html"

    def get_context_data(self, result_form=None, highlight_missing=False, **kwargs):
        ctx = super().get_context_data(**kwargs)
        sched = self.get_assigned_schedule()
        req = sched.request
        items = req.items.select_related("test_item")
        can_enter = req.status == S.IN_PROGRESS
        if can_enter and result_form is None:
            result_form = ResultForm(items=items)
        ctx.update(
            sched=sched,
            req=req,
            items=items,
            logs=req.status_logs.select_related("actor"),
            can_start=req.status == S.SCHEDULED,
            can_enter=can_enter,
            result_form=result_form,
            highlight_missing=highlight_missing,
        )
        return ctx


class ScheduleStartView(AssignedScheduleMixin, View):
    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        sched = self.get_assigned_schedule()
        try:
            services.start(sched.request, request.user)
            messages.success(request, f"已開始 {sched.request.request_no} 的實驗。")
        except TransitionError as e:
            messages.error(request, str(e))
        return staff_detail_redirect(sched)


class ScheduleResultsView(ScheduleDetailView):
    """暫存結果；按「完成」時先暫存再完成，完成失敗時已填的值仍保留。只接受 POST。"""

    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        sched = self.get_assigned_schedule()
        req = sched.request
        form = ResultForm(request.POST, items=req.items.select_related("test_item"))
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(result_form=form))
        try:
            services.save_results(req, request.user, form.results())
        except TransitionError as e:
            messages.error(request, str(e))
            return staff_detail_redirect(sched)
        if request.POST.get("action") != "finish":
            messages.success(request, "結果已暫存。")
            return staff_detail_redirect(sched)
        try:
            services.finish(req, request.user)
        except TransitionError as e:
            messages.error(request, f"{e}結果已暫存。")
            return self.render_to_response(self.get_context_data(result_form=form, highlight_missing=True))
        messages.success(request, f"已完成 {req.request_no} 的實驗。")
        return staff_detail_redirect(sched)
