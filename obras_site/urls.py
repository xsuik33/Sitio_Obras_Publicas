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
    path("obras/<uuid:obra_id>/reportes/", views.reportes_lista, name="reportes_lista"),
    path("obras/<uuid:obra_id>/reportes/nuevo/", views.reporte_nuevo, name="reporte_nuevo"),
    path("obras/<uuid:obra_id>/reportes/<uuid:reporte_id>/", views.reporte_detalle, name="reporte_detalle"),
    path("obras/<uuid:obra_id>/reportes/<uuid:reporte_id>/editar/", views.reporte_editar, name="reporte_editar"),
    path("obras/<uuid:obra_id>/reportes/<uuid:reporte_id>/validar/", views.reporte_validar, name="reporte_validar"),
    path("obras/<uuid:obra_id>/catalogo/", views.catalogo_ver, name="catalogo_ver"),
    path("obras/<uuid:obra_id>/catalogo/editar/", views.catalogo_editar, name="catalogo_editar"),
    path("usuarios/", views.usuarios_lista, name="usuarios_lista"),
    path("usuarios/nuevo/", views.usuario_nuevo, name="usuario_nuevo"),
    path("usuarios/<uuid:usuario_id>/", views.usuario_detalle, name="usuario_detalle"),
    path("cuenta/password/", views.CambiarPasswordView.as_view(), name="cambiar_password"),
    path("salud/", views.salud, name="salud"),
    path("admin/", admin.site.urls),
]
