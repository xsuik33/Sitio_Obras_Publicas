"""
Reglas de la bitácora de obra (CU-09).

Firmantes: Residente, Supervisor y Superintendente Contratista (el Administrador y el Directivo no firman).
  - Firman los roles que tienen al menos una persona activa asignada en la obra.
  - Quien escribe la nota la firma automáticamente con cada rol firmante que tenga en la obra.
  - Firmada cuando todos los roles requeridos firmaron; Rechazada en cuanto uno rechaza (con motivo).
  - Cada rol decide UNA vez; un administrador puede corregir cualquier firma (incluso regresarla a Pendiente).
Edición:
  - El autor edita o borra la nota mientras nadie más la haya firmado o rechazado.
  - Si fue rechazada, el autor la corrige y vuelve a Pendiente: se reinician las firmas de los demás.
  - El folio (consecutivo por obra) lo asigna la BD y nunca se reutiliza.
"""
from django.conf import settings
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from .correo import notificar
from .models import FirmaNotaBitacora, NotaBitacora, UsuarioProyectoRol
from .permisos import CREAR_NOTA, obras_de, puede, roles_en

ROL_RESIDENTE = "Residente"
ROL_SUPERVISOR = "Supervisor"
ROL_SUPERINTENDENTE = "Superintendente Contratista"
ROLES_FIRMANTES = (ROL_RESIDENTE, ROL_SUPERVISOR, ROL_SUPERINTENDENTE)
ETIQUETA = {ROL_RESIDENTE: "Residente", ROL_SUPERVISOR: "Supervisor", ROL_SUPERINTENDENTE: "Superintendente"}

PENDIENTE, FIRMADA, RECHAZADA = "Pendiente", "Firmada", "Rechazada"


# ---------------------------------------------------------------------------
# Firmas y estado
# ---------------------------------------------------------------------------

def firmas_vigentes(nota):
    """{rol: FirmaNotaBitacora | None}: la última decisión de cada rol (None si está pendiente)."""
    resultado = {rol: None for rol in ROLES_FIRMANTES}
    filas = (
        FirmaNotaBitacora.objects.filter(nota_bitacora=nota, rol_firmante__in=ROLES_FIRMANTES)
        .select_related("usuario_firmante")
        .order_by("fecha_firma")
    )
    for f in filas:
        resultado[f.rol_firmante] = f if f.decision in (FIRMADA, RECHAZADA) else None
    return resultado


def decisiones(vigentes):
    return {rol: (f.decision if f else None) for rol, f in vigentes.items()}


def roles_presentes(obra):
    """Roles firmantes con al menos una persona activa en la obra."""
    return set(
        UsuarioProyectoRol.objects.filter(obra=obra, rol__nombre__in=ROLES_FIRMANTES, usuario__activo=True)
        .values_list("rol__nombre", flat=True)
    )


def roles_requeridos(obra, vigentes):
    """Roles cuya firma se necesita: los presentes en la obra y los que ya decidieron."""
    presentes = roles_presentes(obra) | {r for r, f in vigentes.items() if f}
    return [r for r in ROLES_FIRMANTES if r in presentes]


def estado_calculado(vigentes, requeridos):
    decs = decisiones(vigentes)
    if RECHAZADA in decs.values():
        return RECHAZADA
    if requeridos and all(decs[r] == FIRMADA for r in requeridos):
        return FIRMADA
    return PENDIENTE


def _lista(roles):
    return " y ".join(ETIQUETA[r] for r in roles)


def estado_legible(nota, vigentes=None, requeridos=None):
    """Texto claro: quién firmó, quién rechazó y quién falta."""
    vigentes = vigentes if vigentes is not None else firmas_vigentes(nota)
    requeridos = requeridos if requeridos is not None else roles_requeridos(nota.obra, vigentes)
    decs = decisiones(vigentes)
    firmaron = [r for r in requeridos if decs[r] == FIRMADA]
    rechazaron = [r for r in requeridos if decs[r] == RECHAZADA]
    faltan = [r for r in requeridos if decs[r] is None]
    if not requeridos:
        return "Sin firmantes asignados en la obra"
    if rechazaron:
        texto = f"Rechazada por {_lista(rechazaron)}"
        return texto + (f" · firmó {_lista(firmaron)}" if firmaron else "")
    if not faltan:
        return f"Firmada por {_lista(firmaron)}"
    if firmaron:
        return f"Firmó {_lista(firmaron)} · falta {_lista(faltan)}"
    return f"Pendiente · faltan {_lista(faltan)}"


def _registrar(nota, usuario, rol, decision, comentarios=None):
    # Hora explícita (no now() de la transacción): ordena bien varias firmas guardadas en la misma operación.
    return FirmaNotaBitacora.objects.create(
        nota_bitacora=nota, usuario_firmante=usuario, rol_firmante=rol, decision=decision,
        comentarios=comentarios or None, fecha_firma=timezone.now(),
    )


def _recalcular(nota):
    vigentes = firmas_vigentes(nota)
    nota.estado_firma = estado_calculado(vigentes, roles_requeridos(nota.obra, vigentes))
    nota.save(update_fields=["estado_firma"])
    return vigentes


def _firmar_como_autor(nota, autor):
    """El autor firma con cada rol firmante que tenga en la obra."""
    for rol in ROLES_FIRMANTES:
        if rol in roles_en(autor, nota.obra):
            _registrar(nota, autor, rol, FIRMADA, "Firma del autor de la nota.")


# ---------------------------------------------------------------------------
# Quién puede hacer qué
# ---------------------------------------------------------------------------

def roles_que_firma(usuario, obra, nota, vigentes=None, es_admin=False):
    """Roles en nombre de los cuales el usuario puede registrar ahora una firma o un rechazo."""
    vigentes = vigentes if vigentes is not None else firmas_vigentes(nota)
    requeridos = roles_requeridos(obra, vigentes)
    if es_admin:
        return requeridos
    if nota.estado_firma != PENDIENTE:
        return []
    propios = roles_en(usuario, obra)
    return [r for r in requeridos if r in propios and vigentes[r] is None]


def firmada_por_otros(nota, vigentes=None):
    vigentes = vigentes if vigentes is not None else firmas_vigentes(nota)
    return any(f and f.usuario_firmante_id != nota.usuario_creador_id for f in vigentes.values())


def puede_editar(usuario, obra, nota, vigentes=None):
    """El autor, mientras nadie más haya firmado, o para corregirla si fue rechazada."""
    if nota.usuario_creador_id != usuario.pk or not puede(usuario, obra, CREAR_NOTA):
        return False
    return nota.estado_firma == RECHAZADA or (
        nota.estado_firma == PENDIENTE and not firmada_por_otros(nota, vigentes)
    )


def puede_borrar(usuario, obra, nota, vigentes=None):
    """El autor, mientras nadie más la haya firmado o rechazado."""
    if nota.usuario_creador_id != usuario.pk or not puede(usuario, obra, CREAR_NOTA):
        return False
    return nota.estado_firma == PENDIENTE and not firmada_por_otros(nota, vigentes)


# ---------------------------------------------------------------------------
# Operaciones (llamar dentro de transaction.atomic)
# ---------------------------------------------------------------------------

def crear_nota(nota, autor):
    """Guarda una nota nueva (el folio lo pone la BD), la firma el autor y avisa a los demás firmantes."""
    nota.usuario_creador = autor
    nota.save()
    nota.refresh_from_db(fields=["folio", "fecha_nota"])
    _firmar_como_autor(nota, autor)
    _recalcular(nota)
    _avisar_por_firmar(nota, autor, "registró")
    return nota


def guardar_correccion(nota, autor):
    """
    Guarda la edición del autor. Si estaba rechazada, vuelve a Pendiente: se reinician las firmas de los
    demás, el autor la firma de nuevo y se avisa a quienes faltan.
    """
    era_rechazada = nota.estado_firma == RECHAZADA
    nota.fecha_modificacion = timezone.now()
    nota.save(update_fields=["tipo", "descripcion", "nota_relacionada", "fecha_modificacion"])
    if not era_rechazada:
        return nota
    propios = roles_en(autor, nota.obra)
    for rol, firma in firmas_vigentes(nota).items():
        if rol in propios:
            _registrar(nota, autor, rol, FIRMADA, "Firma del autor al corregir la nota.")
        elif firma:
            _registrar(nota, autor, rol, PENDIENTE, "Firma reiniciada: la nota se corrigió y se reenvió.")
    _recalcular(nota)
    _avisar_por_firmar(nota, autor, "corrigió y reenvió")
    return nota


def registrar_firma(nota, usuario, rol, decision, comentarios):
    """Guarda la decisión de un rol y recalcula el estado. La nota debe estar bloqueada (select_for_update)."""
    anterior = nota.estado_firma
    _registrar(nota, usuario, rol, decision, comentarios)
    _recalcular(nota)
    if nota.estado_firma != anterior and nota.estado_firma in (FIRMADA, RECHAZADA):
        _avisar_resultado(nota, comentarios)
    return nota


# ---------------------------------------------------------------------------
# Correos
# ---------------------------------------------------------------------------

def _enlace(nota):
    if not settings.SITE_URL:
        return ""
    return "\n\n" + settings.SITE_URL + reverse("nota_detalle", args=[nota.obra_id, nota.pk])


def _correos_de(obra, roles, excepto=None):
    asignaciones = UsuarioProyectoRol.objects.filter(obra=obra, rol__nombre__in=roles, usuario__activo=True)
    if excepto is not None:
        asignaciones = asignaciones.exclude(usuario=excepto)
    return list(asignaciones.values_list("usuario__correo", flat=True))


def _avisar_por_firmar(nota, autor, verbo):
    vigentes = firmas_vigentes(nota)
    faltan = [r for r in roles_requeridos(nota.obra, vigentes) if vigentes[r] is None]
    if not faltan or nota.estado_firma != PENDIENTE:
        return
    notificar(
        _correos_de(nota.obra, faltan, excepto=autor),
        f"Nota de bitácora por firmar · {nota.obra.nombre} · folio {nota.folio}",
        f"{autor} {verbo} la nota {nota.folio} ({nota.tipo.nombre}) en la bitácora de la obra "
        f"«{nota.obra.nombre}».\n\nContenido de la nota:\n{nota.descripcion}\n\n"
        f"Entra al sistema para firmarla o rechazarla (Pendientes).{_enlace(nota)}",
    )


def _avisar_resultado(nota, comentarios):
    autor = nota.usuario_creador
    resultado = nota.estado_firma.lower()
    texto = f"La nota {nota.folio} de la bitácora de la obra «{nota.obra.nombre}» fue {resultado}.\n" \
            f"Estado: {estado_legible(nota)}."
    if nota.estado_firma == RECHAZADA:
        if comentarios:
            texto += f"\n\nMotivo:\n{comentarios}"
        texto += "\n\nCorrígela en el sistema; al guardarla se enviará de nuevo a firma."
    notificar(
        [autor.correo] if autor.activo else [],
        f"Nota de bitácora {resultado} · {nota.obra.nombre} · folio {nota.folio}",
        texto + _enlace(nota),
    )


# ---------------------------------------------------------------------------
# Bandeja «Pendientes»
# ---------------------------------------------------------------------------

def pendientes_de(usuario):
    """Notas que requieren acción del usuario: ('firmar', 'corregir')."""
    firmar, corregir = [], []
    for obra in obras_de(usuario).order_by("nombre"):
        mis_roles = [r for r in ROLES_FIRMANTES if r in roles_en(usuario, obra)]
        if mis_roles:
            candidatas = NotaBitacora.objects.filter(obra=obra, estado_firma=PENDIENTE)
            for n in candidatas.select_related("obra", "tipo", "usuario_creador").order_by("folio"):
                vigentes = firmas_vigentes(n)
                requeridos = roles_requeridos(obra, vigentes)
                if any(r in requeridos and vigentes[r] is None for r in mis_roles):
                    firmar.append((n, estado_legible(n, vigentes, requeridos)))
        propias = NotaBitacora.objects.filter(obra=obra, usuario_creador=usuario, estado_firma=RECHAZADA)
        for n in propias.select_related("obra", "tipo", "usuario_creador").order_by("folio"):
            corregir.append((n, estado_legible(n)))
    return firmar, corregir


def buscar(obra, estado="", tipo="", texto=""):
    consulta = NotaBitacora.objects.filter(obra=obra).select_related("tipo", "usuario_creador", "obra")
    if estado:
        consulta = consulta.filter(estado_firma=estado)
    if tipo:
        consulta = consulta.filter(tipo_id=tipo)
    if texto:
        filtro = Q(descripcion__icontains=texto)
        if texto.isdigit():
            filtro |= Q(folio=int(texto))
        consulta = consulta.filter(filtro)
    return consulta.order_by("-folio")
