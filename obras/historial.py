"""
Historial de la obra (postcondición de CU-11 y RF-25): quién registró o cambió qué y cuándo.

Se guarda en django_admin_log (también visible en /admin/), siempre ligado a la obra, con un mensaje legible.
Registra: alta y cambios de la obra, del contrato y de los anticipos, y la carga o el borrado de documentos.
"""
from django.contrib.admin.models import ADDITION, CHANGE, DELETION, LogEntry
from django.contrib.contenttypes.models import ContentType

from .models import Obra

ALTA, CAMBIO, BAJA = ADDITION, CHANGE, DELETION


def registrar(actor, obra, bandera, mensaje):
    LogEntry.objects.log_actions(actor.pk, Obra.objects.filter(pk=obra.pk), bandera, change_message=mensaje,
                                 single_object=True)


def de_obra(obra, limite=None):
    consulta = LogEntry.objects.filter(content_type=ContentType.objects.get_for_model(Obra), object_id=str(obra.pk)) \
        .select_related("user").order_by("-action_time")
    return consulta[:limite] if limite else consulta


def cambios(form):
    """Lista legible de los campos que cambió un ModelForm."""
    return ", ".join(str(form.fields[c].label).lower() for c in form.changed_data if c in form.fields)
