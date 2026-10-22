from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path

from obras import views

urlpatterns = [
    path("", views.inicio, name="inicio"),
    path("login/", auth_views.LoginView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("obras/", views.mis_obras, name="mis_obras"),
    path("obras/<uuid:obra_id>/", views.obra_detalle, name="obra_detalle"),
    path("obras/<uuid:obra_id>/roles/", views.roles_obra, name="roles_obra"),
    path("salud/", views.salud, name="salud"),
    path("admin/", admin.site.urls),
]
