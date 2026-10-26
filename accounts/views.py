from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from django.views import View
from django.views.generic import FormView, TemplateView

from .forms import RegisterForm
from .roles import ROLE_DASHBOARD, get_role


class HomeView(TemplateView):
    """首頁；已登入者導向自己的工作台。"""

    template_name = "home.html"

    def get(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect("dashboard")
        return super().get(request, *args, **kwargs)


class DashboardRedirectView(LoginRequiredMixin, View):
    """登入後依角色導向對應的工作台。"""

    def get(self, request):
        role = get_role(request.user)
        if role is None:
            raise PermissionDenied("此帳號尚未設定角色，請聯絡管理者。")
        return redirect(ROLE_DASHBOARD[role])


class RegisterView(FormView):
    template_name = "accounts/register.html"
    form_class = RegisterForm

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect("dashboard")
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        user = form.save()
        login(self.request, user, backend="django.contrib.auth.backends.ModelBackend")
        messages.success(self.request, f"註冊成功，歡迎 {user.first_name}！")
        return redirect("dashboard")
