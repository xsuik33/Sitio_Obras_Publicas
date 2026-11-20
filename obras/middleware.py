"""Candado de las cuentas de solo consulta: rechaza cualquier petición que modifique algo."""
from django.contrib import messages
from django.shortcuts import redirect
from django.urls import reverse

METODOS_LECTURA = {"GET", "HEAD", "OPTIONS"}


class SoloLecturaMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        usuario = getattr(request, "user", None)
        if (request.method not in METODOS_LECTURA and usuario is not None and usuario.is_authenticated
                and usuario.es_consulta and request.path != reverse("logout")):
            messages.error(request, "Esta es una cuenta de solo consulta: puede ver todo, pero no modificar nada.")
            return redirect(request.META.get("HTTP_REFERER") or reverse("mis_obras"), permanent=False)
        return self.get_response(request)
