"""View 層的共用權限檢查（簡報 §3）。

權限一律在這裡判斷，template 只負責顯示。
- 未登入：導向登入頁。
- 已登入但角色不符：403。
- 存取他人資料的 404 由各 view 以使用者過濾 queryset 達成。
"""
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied

from .roles import ADMIN, APPLICANT, LAB_STAFF, get_role


class RoleRequiredMixin(LoginRequiredMixin):
    required_role = None

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if get_role(request.user) != self.required_role:
            raise PermissionDenied("您沒有進入此頁面的權限。")
        return super().dispatch(request, *args, **kwargs)


class AdminRequiredMixin(RoleRequiredMixin):
    required_role = ADMIN


class LabStaffRequiredMixin(RoleRequiredMixin):
    required_role = LAB_STAFF


class ApplicantRequiredMixin(RoleRequiredMixin):
    required_role = APPLICANT
