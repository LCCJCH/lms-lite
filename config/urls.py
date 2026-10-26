from django.contrib import admin
from django.urls import include, path

from accounts.views import DashboardRedirectView, HomeView

urlpatterns = [
    path("", HomeView.as_view(), name="home"),
    path("accounts/", include("accounts.urls")),
    path("dashboard/", DashboardRedirectView.as_view(), name="dashboard"),
    path("dashboard/", include("lab.urls")),
    path("admin/", admin.site.urls),
]
