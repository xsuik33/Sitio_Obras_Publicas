from django.db import connection
from django.http import HttpResponse, JsonResponse


def inicio(request):
    return HttpResponse(
        "<h1>Sistema de Obras</h1><p>Servidor activo. Panel: <a href='/admin/'>/admin/</a></p>"
    )


def salud(request):
    """Comprobación de salud para Render: verifica la conexión a la base de datos."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
        cursor.fetchone()
    return JsonResponse({"estado": "ok"})
