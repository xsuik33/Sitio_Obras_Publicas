from .cuentas import puede_administrar_cuentas


def navegacion(request):
    usuario = request.user
    return {"puede_cuentas": bool(usuario.is_authenticated and puede_administrar_cuentas(usuario))}
