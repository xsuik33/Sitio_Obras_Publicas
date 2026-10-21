from django.contrib import admin
from django.urls import path

from obras import views

urlpatterns = [
    path("", views.inicio, name="inicio"),
    path("salud/", views.salud, name="salud"),
    path("admin/", admin.site.urls),
]
