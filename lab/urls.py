from django.urls import path

from . import views

app_name = "lab"

urlpatterns = [
    path("admin/", views.AdminDashboardView.as_view(), name="admin_dashboard"),
    path("admin/requests/", views.AdminRequestListView.as_view(), name="admin_request_list"),
    path("admin/requests/<int:pk>/", views.AdminRequestDetailView.as_view(), name="admin_request_detail"),
    path("admin/requests/<int:pk>/accept/", views.AdminAcceptView.as_view(), name="admin_request_accept"),
    path("admin/requests/<int:pk>/return/", views.AdminReturnView.as_view(), name="admin_request_return"),
    path("admin/requests/<int:pk>/schedule/", views.AdminScheduleView.as_view(), name="admin_request_schedule"),
    path(
        "admin/requests/<int:pk>/reschedule/",
        views.AdminRescheduleView.as_view(),
        name="admin_request_reschedule",
    ),
    path("admin/requests/<int:pk>/publish/", views.AdminPublishView.as_view(), name="admin_request_publish"),
    path("lab/", views.LabDashboardView.as_view(), name="lab_dashboard"),
    path("lab/schedules/<int:pk>/", views.ScheduleDetailView.as_view(), name="staff_schedule_detail"),
    path("lab/schedules/<int:pk>/start/", views.ScheduleStartView.as_view(), name="staff_schedule_start"),
    path("lab/schedules/<int:pk>/results/", views.ScheduleResultsView.as_view(), name="staff_schedule_results"),
    path("applicant/", views.ApplicantDashboardView.as_view(), name="applicant_dashboard"),
    path("applicant/history/", views.ApplicantHistoryView.as_view(), name="applicant_history"),
    path("applicant/requests/new/", views.RequestCreateView.as_view(), name="applicant_request_new"),
    path("applicant/requests/<int:pk>/", views.RequestDetailView.as_view(), name="applicant_request_detail"),
    path("applicant/requests/<int:pk>/edit/", views.RequestUpdateView.as_view(), name="applicant_request_edit"),
    path("applicant/requests/<int:pk>/submit/", views.RequestSubmitView.as_view(), name="applicant_request_submit"),
    path(
        "applicant/requests/<int:pk>/withdraw/",
        views.RequestWithdrawView.as_view(),
        name="applicant_request_withdraw",
    ),
    path("applicant/requests/<int:pk>/delete/", views.RequestDeleteView.as_view(), name="applicant_request_delete"),
]
