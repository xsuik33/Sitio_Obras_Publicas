"""PDF de los reportes diario y mensual (reportlab)."""
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
        ("Incidentes de seguridad", reporte.incidentes_seguridad),
        ("Observaciones y acciones correctivas", reporte.observaciones),
    ):
        if texto:
            historia += [_p(titulo, h2), _p(texto, normal)]

    if desviacion and desviacion.get("critico"):
        dias = desviacion["dias_atraso"]
        historia.append(_p(f"Atraso crítico · {dias} día{'s' if dias != 1 else ''} detrás del programa", h2))
        if reporte.atraso_solicitud:
            historia.append(_p(f"Solicitud: {reporte.atraso_solicitud}", normal))
        historia.append(_p(reporte.atraso_causa or "Sin causa documentada.", normal if reporte.atraso_causa else suave))

    notas = list(reporte.notas.select_related("tipo").order_by("folio"))
    if notas:
        historia.append(_p("Notas de bitácora vinculadas", h2))
        for n in notas:
            historia.append(_p(f"Nota {n.folio} · {n.tipo.nombre} · {n.estado_firma}: {n.descripcion[:160]}", normal))

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


def _tabla(filas, anchos, encabezado=False, derecha=()):
    t = Table(filas, colWidths=anchos, repeatRows=1 if encabezado else 0)
    estilo = [
        ("FONTSIZE", (0, 0), (-1, -1), 9), ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d9dde4")),
    ]
    if encabezado:
        estilo += [("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f5f6f8")),
                   ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#667085"))]
    estilo += [("ALIGN", (c, 0), (c, -1), "RIGHT") for c in derecha]
    t.setStyle(TableStyle(estilo))
    return t


def reporte_mensual_pdf(reporte, mes, estado, c, congelado, validaciones):
    """PDF del reporte mensual (CU-04). `c` es el consolidado de obras.mensuales.consolidar."""
    base = getSampleStyleSheet()
    normal = ParagraphStyle("n", parent=base["Normal"], fontSize=9.5, leading=13)
    suave = ParagraphStyle("s", parent=normal, textColor=colors.HexColor("#667085"))
    h1 = ParagraphStyle("h1", parent=base["Title"], fontSize=17, alignment=0, spaceAfter=2)
    h2 = ParagraphStyle("h2", parent=base["Heading2"], fontSize=11.5, spaceBefore=10, spaceAfter=3)

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=letter, leftMargin=2 * cm, rightMargin=2 * cm, topMargin=1.8 * cm, bottomMargin=1.8 * cm,
        title=f"Reporte mensual {mes}", author="Sistema de Obras",
    )
    av, di = c["avance"], c["diarios"]
    historia = [
        _p(f"Reporte mensual · {mes}", h1),
        _p(reporte.obra.nombre, normal),
        _p(f"Supervisor: {reporte.supervisor} · Periodo: {c['periodo']['inicio']} – {c['periodo']['corte']} · "
           f"Estado: {estado}", suave),
        _p(("Cifras congeladas al aprobarse" if congelado else "Cifras calculadas") + f" el {c['generado']}.", suave),
        Spacer(1, 8),
    ]
    pct = lambda v: f"{v} %" if v else "—"  # noqa: E731
    historia.append(_tabla([
        ["Avance real al corte", pct(av["corte"]), "Avance en el mes", pct(av["mes"])],
        ["Programado al corte", pct(av["programado"]), "Desviación", av["texto"] or "—"],
        ["Reportes diarios", f"{di['total']} de {c['periodo']['dias']} días", "Aprobados", str(di["aprobados"])],
    ], [3.6 * cm, 4.9 * cm, 3.6 * cm, 4.9 * cm]))

    if c["advertencias"]:
        historia += [_p("Advertencias", h2)] + [_p(f"• {a}", normal) for a in c["advertencias"]]

    historia += [_p("Resumen del mes", h2), _p(reporte.resumen or "—", normal)]
    if reporte.observaciones:
        historia += [_p("Observaciones", h2), _p(reporte.observaciones, normal)]

    if di["filas"]:
        historia.append(_p("Reportes diarios del periodo", h2))
        filas = [["Fecha", "Avance", "Estatus"]] + [[d["fecha"], f"{d['avance']} %", d["estatus"]] for d in di["filas"]]
        historia.append(_tabla(filas, [5 * cm, 4 * cm, 8 * cm], encabezado=True, derecha=(1,)))
        if di["fechas_sin_reporte"]:
            historia.append(_p("Días sin reporte: " + ", ".join(di["fechas_sin_reporte"]), suave))
    if c["clima"]:
        historia.append(_p("Clima: " + ", ".join(f"{n} ({k})" for n, k in c["clima"]), suave))

    for titulo, clave in (("Incidencias", "incidencias"), ("Incidentes de seguridad", "seguridad")):
        if c[clave]:
            historia.append(_p(titulo, h2))
            historia += [_p(f"{i['fecha']} · {i['texto']}", normal) for i in c[clave]]

    est = c["estimaciones"]
    historia.append(_p("Estimaciones", h2))
    if est:
        if est["filas"]:
            filas = [["Folio", "Corte", "Subtotal", "Estatus"]] + [
                [e["folio"], e["corte"], e["subtotal"], e["estatus"]] for e in est["filas"]]
            historia.append(_tabla(filas, [4.5 * cm, 3.5 * cm, 4.5 * cm, 4.5 * cm], encabezado=True, derecha=(2,)))
            historia.append(Spacer(1, 4))
        resumen = [["Aprobado en el mes", est["aprobado_mes"]], ["Acumulado aprobado al corte", est["acumulado"]],
                   ["Monto del contrato", est["monto"]]]
        if est["avance_financiero"]:
            resumen.append(["Avance financiero", f"{est['avance_financiero']} %"])
        historia.append(_tabla(resumen, [10 * cm, 7 * cm], derecha=(1,)))
    else:
        historia.append(_p("La obra no tiene contrato registrado.", suave))

    bt = c["bitacora"]
    historia.append(_p("Bitácora del periodo", h2))
    if bt["filas"]:
        filas = [["Folio", "Tipo", "Nota", "Firma"]] + [
            [str(n["folio"]), n["tipo"], _p(n["texto"], normal), n["estado"]] for n in bt["filas"]]
        historia.append(_tabla(filas, [1.5 * cm, 2.8 * cm, 10.2 * cm, 2.5 * cm], encabezado=True))
    else:
        historia.append(_p("Sin notas de bitácora en el periodo.", suave))

    historia.append(_p("Revisión", h2))
    for v in validaciones:
        rol = {"Superintendente Contratista": "Superintendente"}.get(v.rol_validador, v.rol_validador)
        linea = f"{v.estatus.nombre} · {rol} · {v.usuario_validador} · {v.fecha_validacion:%d/%m/%Y %H:%M}"
        historia.append(_p(linea + (f"\n{v.comentarios}" if v.comentarios else ""), normal))
    if not validaciones:
        historia.append(_p("Todavía no ha sido revisado.", suave))

    doc.build(historia)
    return buffer.getvalue()
