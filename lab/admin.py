from django.contrib import admin

from .models import Request, RequestItem, RequestStatusLog, Schedule, TestItem


@admin.register(TestItem)
class TestItemAdmin(admin.ModelAdmin):
    list_display = ("name", "category", "unit", "loq", "is_active", "updated_at")
    list_filter = ("category", "is_active")
    search_fields = ("name",)


class RequestItemInline(admin.TabularInline):
    model = RequestItem
    extra = 0
    readonly_fields = ("test_item", "result_value", "result_note")
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


class ScheduleInline(admin.StackedInline):
    model = Schedule
    extra = 0
    readonly_fields = ("scheduled_date", "assigned_staff", "scheduled_by", "started_at", "completed_at")
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


class RequestStatusLogInline(admin.TabularInline):
    model = RequestStatusLog
    extra = 0
    readonly_fields = ("created_at", "from_status", "to_status", "actor", "comment")
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Request)
class RequestAdmin(admin.ModelAdmin):
    """僅供 superuser 除錯；狀態相關欄位唯讀，狀態只能經由 lab.services 修改。"""

    list_display = ("request_no", "sample_name", "applicant", "status", "submitted_at")
    list_filter = ("status",)
    search_fields = ("request_no", "sample_name")
    readonly_fields = ("request_no", "status", "return_reason", "submitted_at", "published_at", "applicant")
    inlines = [ScheduleInline, RequestItemInline, RequestStatusLogInline]

    def has_add_permission(self, request):
        return False
