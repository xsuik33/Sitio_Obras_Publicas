"""PDF del reporte diario (reportlab)."""
from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


def _p(texto, estilo):
    return Paragraph(escape(str(texto)).replace("\n", "<br/>"), estilo)


def _dinero(valor):
    return f"$ {valor:,.2f}"


def reporte_diario_pdf(reporte, estado, desviacion, financiero, validaciones):
    base = getSampleStyleSheet()
    normal = ParagraphStyle("n", parent=base["Normal"], fontSize=9.5, leading=13)
    suave = ParagraphStyle("s", parent=normal, textColor=colors.HexColor("#667085"))
    h1 = ParagraphStyle("h1", parent=base["Title"], fontSize=17, alignment=0, spaceAfter=2)
    h2 = ParagraphStyle("h2", parent=base["Heading2"], fontSize=11.5, spaceBefore=10, spaceAfter=3)

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=letter, leftMargin=2 * cm, rightMargin=2 * cm, topMargin=1.8 * cm, bottomMargin=1.8 * cm,
        title=f"Reporte diario {reporte.fecha_reporte:%d/%m/%Y}", author="Sistema de Obras",
    )
    historia = [
        _p(f"Reporte diario · {reporte.fecha_reporte:%d/%m/%Y}", h1),
        _p(f"{reporte.obra.nombre}", normal),
        _p(f"Supervisor: {reporte.supervisor} · Estado: {estado}", suave),
        Spacer(1, 8),
    ]

    jornada = "—"
    if reporte.jornada_inicio:
        fin = reporte.jornada_fin.strftime("%H:%M") if reporte.jornada_fin else "?"
        jornada = f"{reporte.jornada_inicio:%H:%M} – {fin}"
    clima = reporte.clima or "—"
    if reporte.temperatura_c is not None:
        clima += f" · {reporte.temperatura_c} °C"
    datos = [
        ["Avance físico real", f"{reporte.avance_fisico_real_porcentaje:.2f} %", "Jornada", jornada],
        ["Clima", clima, "Fecha de registro", f"{reporte.fecha_registro:%d/%m/%Y %H:%M}"],
    ]
    if desviacion:
        datos.append(["Avance programado", f"{desviacion['programado']} %", "Desviación", desviacion["texto"]])
    tabla = Table(datos, colWidths=[3.4 * cm, 5 * cm, 3.4 * cm, 5 * cm])
    tabla.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 9.5), ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#667085")),
        ("TEXTCOLOR", (2, 0), (2, -1), colors.HexColor("#667085")),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d9dde4")), ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    historia.append(tabla)

    for titulo, texto in (
        ("Actividades realizadas", reporte.actividades_realizadas), ("Incidencias", reporte.incidencias),
        ("Incidentes de seguridad", reporte.incidentes_seguridad), ("Observaciones", reporte.observaciones),
    ):
        if texto:
            historia += [_p(titulo, h2), _p(texto, normal)]

    if financiero:
        historia.append(_p("Resumen financiero", h2))
        filas = [
            ["Monto del contrato", _dinero(financiero["monto"])],
            ["Avance físico × monto del contrato", _dinero(financiero["fisico"])],
            ["Aprobado en estimaciones", _dinero(financiero["estimado"])],
            ["Diferencia por estimar", _dinero(financiero["por_estimar"])],
        ]
        t = Table(filas, colWidths=[9 * cm, 6.8 * cm])
        t.setStyle(TableStyle([
            ("FONTSIZE", (0, 0), (-1, -1), 9.5), ("ALIGN", (1, 0), (1, -1), "RIGHT"),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d9dde4")),
        ]))
        historia.append(t)

    historia.append(_p("Revisión", h2))
    if validaciones:
        for v in validaciones:
            rol = {"Superintendente Contratista": "Superintendente"}.get(v.rol_validador, v.rol_validador or "")
            linea = f"{v.estatus.nombre} · {rol + ' · ' if rol else ''}{v.usuario_validador} · {v.fecha_validacion:%d/%m/%Y %H:%M}"
            if v.comentarios:
                linea += f"\n{v.comentarios}"
            historia.append(_p(linea, normal))
            historia.append(Spacer(1, 3))
    else:
        historia.append(_p("Todavía no ha sido revisado.", suave))

    doc.build(historia)
    return buffer.getvalue()
