"""
Genera secciones/diccionario.tex (diccionario de datos) a partir de db/esquema_obras.sql.

Uso (desde Doc/latex):  python generar_diccionario.py
Así el documento siempre coincide con el esquema real: si cambia la base de datos, se vuelve a generar.
"""
import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
ESQUEMA = RAIZ / "db" / "esquema_obras.sql"
SALIDA = Path(__file__).resolve().parent / "secciones" / "diccionario.tex"

MODULOS = [
    ("Módulo núcleo", "Alcance físico y financiero de la obra: la obra, su contrato y anticipos, el catálogo de conceptos y el programa de obra.",
     ["obras", "contratos", "anticipos", "catalogo_conceptos", "programa_obra"]),
    ("Módulo de seguridad", "Cuentas, roles y la asignación de roles por obra, que limita los permisos de cada persona a las obras en que participa.",
     ["usuarios", "roles", "usuarios_proyectos_roles"]),
    ("Módulo de operación", "Información que se genera en la obra: estimaciones, reportes diarios y mensuales y bitácora.",
     ["estimaciones", "estimaciones_detalles", "reportes_diarios", "reportes_diarios_notas", "reportes_mensuales", "notas_bitacora"]),
    ("Módulo de auditoría y archivos", "Catálogos de estatus, historial no editable de validaciones y firmas, y repositorio de documentos y evidencias.",
     ["catalogo_estatus_validacion", "catalogo_tipos_nota", "validaciones_reportes_diarios", "validaciones_reportes_mensuales",
      "validaciones_estimaciones", "firmas_notas_bitacora", "documentos", "evidencias"]),
]

TABLAS = {
    "obras": "Obra pública: datos generales y fechas oficiales.",
    "contratos": "Contrato de la obra (uno por obra) con montos, porcentajes y garantías.",
    "anticipos": "Ministraciones del anticipo del contrato.",
    "catalogo_conceptos": "Conceptos de trabajo de la obra con su volumen y precio unitario.",
    "programa_obra": "Avance físico programado acumulado en fechas clave; entre ellas se interpola.",
    "usuarios": "Cuentas de usuario.",
    "roles": "Catálogo de roles: Superintendente Contratista, Supervisor, Residente, Directivo y Administrador.",
    "usuarios_proyectos_roles": "Rol de cada usuario en cada obra (una persona puede tener varios).",
    "estimaciones": "Estimación de trabajos ejecutados en un periodo. Los totales los calculan disparadores.",
    "estimaciones_detalles": "Renglones de la estimación: cantidad ejecutada por concepto.",
    "reportes_diarios": "Reporte diario del Supervisor con el avance físico real.",
    "reportes_diarios_notas": "Notas de bitácora vinculadas a un reporte diario.",
    "reportes_mensuales": "Reporte mensual que consolida los reportes diarios del mes.",
    "notas_bitacora": "Notas de la bitácora de obra con folio consecutivo por obra.",
    "catalogo_estatus_validacion": "Estatus: Pendiente, Aprobado, Rechazado, En revisión, Borrador y Con observaciones.",
    "catalogo_tipos_nota": "Tipos de nota de bitácora: Apertura, Instrucción, Solicitud, Avance, Incidencia, Cierre y Otra.",
    "validaciones_reportes_diarios": "Historial de decisiones sobre los reportes diarios.",
    "validaciones_reportes_mensuales": "Historial de decisiones sobre los reportes mensuales.",
    "validaciones_estimaciones": "Historial de decisiones del Supervisor y del Residente sobre las estimaciones.",
    "firmas_notas_bitacora": "Historial de firmas de cada rol sobre las notas de bitácora.",
    "documentos": "Repositorio de documentos de la obra y soporte de estimaciones.",
    "evidencias": "Fotografías y PDF de un reporte diario, un reporte mensual o una nota de bitácora.",
}

DESCRIPCIONES = {
    "id": "Identificador único (UUID versión 4).",
    "fecha_registro": "Fecha y hora del registro.",
    "fecha_modificacion": "Fecha y hora de la última corrección.",
    "observaciones": "Observaciones en texto libre.",
    "descripcion": "Descripción en texto libre.",
    "comentarios": "Comentarios o motivo de la decisión.",
    "estatus_id": "Estatus vigente (catálogo de estatus).",
    "usuario_validador_id": "Usuario que registró la decisión.",
    "fecha_validacion": "Fecha y hora de la decisión.",
    "rol_validador": "Rol con el que se decidió.",
    "obras.nombre": "Nombre de la obra.",
    "obras.ubicacion": "Ubicación de la obra.",
    "obras.fecha_inicio_oficial": "Inicio oficial; define el estado de la obra y el arranque del programa.",
    "obras.fecha_fin_oficial": "Terminación oficial.",
    "obras.ultimo_folio_bitacora": "Contador de folios de la bitácora (lo usa un disparador).",
    "contratos.numero_contrato": "Número del contrato.",
    "contratos.contratista": "Empresa contratista.",
    "contratos.monto_total_contratado": "Monto contratado sin IVA.",
    "contratos.porcentaje_iva": "Porcentaje de IVA.",
    "contratos.porcentaje_retencion": "Retención por derechos (por ejemplo 5 al millar).",
    "contratos.porcentaje_anticipo": "Porcentaje de anticipo (máximo 30 %).",
    "contratos.monto_anticipo": "Monto del anticipo (calculado).",
    "contratos.porcentaje_retencion_atraso": "Retención económica por atraso (LOPSRM art. 46 Bis).",
    "contratos.monto_garantia_cumplimiento": "Garantía de cumplimiento; tope de penas convencionales.",
    "contratos.numero_fianza": "Número de la fianza.",
    "contratos.afianzadora": "Institución afianzadora.",
    "catalogo_conceptos.clave": "Clave del concepto, única por obra.",
    "catalogo_conceptos.capitulo": "Capítulo o partida.",
    "catalogo_conceptos.unidad_medida": "Unidad de medida.",
    "catalogo_conceptos.volumen_total_contratado": "Cantidad contratada.",
    "catalogo_conceptos.precio_unitario": "Precio unitario.",
    "catalogo_conceptos.importe_contratado": "Cantidad por precio unitario (calculado).",
    "estimaciones.folio": "Folio EST-001, EST-002… único por contrato.",
    "estimaciones.numero_estimacion": "Número consecutivo por contrato.",
    "estimaciones.subtotal": "Importe de los trabajos (suma de renglones).",
    "estimaciones.amortizacion_anticipo": "Importe por porcentaje de anticipo (Reglamento art. 143).",
    "estimaciones.retencion": "Retención por derechos (calculada).",
    "estimaciones.iva": "IVA sobre el importe menos la amortización (calculado).",
    "estimaciones.monto_neto": "Neto a pagar (calculado).",
    "estimaciones.fecha_envio": "Fecha y hora en que se envió a revisión.",
    "estimaciones.superintendente_id": "Superintendente que la registró.",
    "contratos.fecha_firma": "Fecha de firma del contrato.",
    "contratos.fecha_inicio": "Inicio de los trabajos según el contrato.",
    "contratos.fecha_fin": "Terminación de los trabajos según el contrato.",
    "anticipos.tipo": "Inicio de obra, Materiales u Otro.",
    "anticipos.monto": "Monto de la ministración.",
    "anticipos.fecha_programada": "Fecha programada de entrega.",
    "anticipos.fecha_pago": "Fecha en que se pagó.",
    "anticipos.estatus": "Programado, Pagado o Cancelado.",
    "programa_obra.fecha": "Fecha clave del programa.",
    "programa_obra.avance_programado_porcentaje": "Avance físico programado acumulado a esa fecha.",
    "usuarios.nombre": "Nombre de la persona.",
    "usuarios.apellidos": "Apellidos de la persona.",
    "usuarios.correo": "Correo; sirve para iniciar sesión y recibir avisos.",
    "usuarios.password_hash": "Contraseña cifrada con función hash; nunca en texto claro.",
    "usuarios.activo": "Una cuenta inactiva no puede iniciar sesión.",
    "usuarios.fecha_creacion": "Fecha y hora de creación de la cuenta.",
    "roles.nombre": "Nombre del rol.",
    "catalogo_estatus_validacion.nombre": "Nombre del estatus.",
    "catalogo_tipos_nota.nombre": "Nombre del tipo de nota.",
    "estimaciones.tipo": "Tipo de estimación (Reglamento art. 130).",
    "estimaciones.periodo_inicio": "Inicio del periodo estimado.",
    "estimaciones.fecha_corte": "Fecha de corte del periodo (máximo un mes).",
    "estimaciones.fecha_presentacion": "Fecha en que se presentó a revisión.",
    "estimaciones.retencion_atraso": "Retención económica por atraso al corte.",
    "estimaciones.devolucion_retencion": "Devolución de retenciones anteriores al regularizarse.",
    "estimaciones.dias_atraso": "Días de atraso al corte respecto del programa.",
    "estimaciones.atraso_no_imputable": "El Superintendente justifica que el atraso no le es imputable.",
    "estimaciones.justificacion_atraso": "Justificación del atraso no imputable.",
    "reportes_diarios.fecha_reporte": "Fecha del reporte (uno por obra y fecha).",
    "reportes_diarios.jornada_inicio": "Hora de inicio de la jornada.",
    "reportes_diarios.jornada_fin": "Hora de fin de la jornada.",
    "reportes_diarios.clima": "Condición del clima.",
    "reportes_diarios.temperatura_c": "Temperatura en grados Celsius.",
    "reportes_diarios.actividades_realizadas": "Actividades realizadas en el día.",
    "reportes_diarios.incidencias": "Incidencias del día.",
    "reportes_diarios.incidentes_seguridad": "Incidentes de seguridad del día.",
    "reportes_diarios.atraso_causa": "Causa del atraso crítico (1 día o más).",
    "reportes_diarios.atraso_solicitud": "Solicitud ante el atraso crítico.",
    "reportes_mensuales.anio": "Año del periodo.",
    "reportes_mensuales.mes": "Mes del periodo (1 a 12).",
    "reportes_mensuales.fecha_corte": "Fecha de corte, dentro del mes reportado.",
    "reportes_mensuales.resumen": "Resumen del mes redactado por el Supervisor.",
    "reportes_mensuales.supervisor_id": "Supervisor que elaboró el reporte.",
    "notas_bitacora.fecha_nota": "Fecha y hora de la nota.",
    "notas_bitacora.usuario_creador_id": "Autor de la nota.",
    "notas_bitacora.tipo_id": "Tipo de nota (catálogo).",
    "notas_bitacora.nota_relacionada_id": "Nota a la que responde.",
    "firmas_notas_bitacora.rol_firmante": "Rol con el que se firmó.",
    "firmas_notas_bitacora.decision": "Firmada, Rechazada o Pendiente (reinicio).",
    "firmas_notas_bitacora.fecha_firma": "Fecha y hora de la firma.",
    "firmas_notas_bitacora.usuario_firmante_id": "Usuario que firmó.",
    "documentos.nombre_archivo": "Nombre del documento.",
    "documentos.tipo_archivo": "Formato del archivo (PDF, DOCX, XLSX, imagen o DWG).",
    "documentos.fecha_subida": "Fecha y hora de la carga.",
    "documentos.categoria": "Tipo de documento.",
    "documentos.usuario_subida_id": "Usuario que lo subió.",
    "documentos.estimacion_id": "Estimación de la que es soporte (opcional).",
    "evidencias.tipo": "Tipo de contenido (imagen o PDF).",
    "evidencias.fecha_subida": "Fecha y hora de la carga.",
    "evidencias.usuario_subida_id": "Usuario que la subió.",
    "estimaciones_detalles.precio_unitario": "Precio unitario; si va vacío se toma el del catálogo (disparador).",
    "estimaciones_detalles.numero_generador": "Número del generador que respalda la cantidad.",
    "estimaciones_detalles.nota_bitacora_id": "Nota de bitácora firmada que autorizó el excedente (Reglamento art. 118).",
    "estimaciones_detalles.cantidad_ejecutada": "Cantidad ejecutada en el periodo.",
    "estimaciones_detalles.cantidad_acumulada_anterior": "Cantidad estimada en estimaciones anteriores (disparador).",
    "estimaciones_detalles.cantidad_acumulada": "Acumulado anterior más el periodo (calculado).",
    "estimaciones_detalles.importe": "Cantidad por precio unitario (calculado).",
    "estimaciones_detalles.es_excedente": "Verdadero si el acumulado rebasa lo contratado (disparador).",
    "reportes_diarios.avance_fisico_real_porcentaje": "Avance físico real acumulado (0 a 100).",
    "reportes_diarios.supervisor_id": "Supervisor que elaboró el reporte.",
    "reportes_mensuales.consolidado": "Cifras del mes congeladas al aprobarse (JSON).",
    "notas_bitacora.folio": "Folio consecutivo por obra; nunca se reutiliza.",
    "notas_bitacora.estado_firma": "Pendiente, Firmada o Rechazada.",
    "documentos.ruta_almacenamiento": "Ruta del archivo en Supabase Storage.",
    "evidencias.ruta": "Ruta del archivo en Supabase Storage.",
}

INICIO_COLUMNA = re.compile(r"^  ([a-z_][a-z0-9_]*)\s+(.+)$")
PALABRAS = r"(PRIMARY KEY|NOT NULL|NULL|DEFAULT|REFERENCES|UNIQUE|CHECK|GENERATED|,|--|$)"


def tex(texto):
    reemplazos = {"\\": r"\textbackslash{}", "_": r"\_", "%": r"\%", "&": r"\&", "#": r"\#", "$": r"\$",
                  "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}
    return "".join(reemplazos.get(c, c) for c in texto)


def tablas(sql):
    for m in re.finditer(r"CREATE TABLE (\w+) \((.*?)\n\);", sql, re.S):
        yield m.group(1), m.group(2).split("\n")


def columnas(lineas):
    actuales = []
    for linea in lineas:
        sin_coment, _, comentario = linea.partition("--")
        m = INICIO_COLUMNA.match(sin_coment.rstrip())
        if m and m.group(1).upper() not in ("CONSTRAINT", "PRIMARY", "UNIQUE", "CHECK"):
            actuales.append({"nombre": m.group(1), "def": m.group(2), "comentario": comentario.strip()})
        elif actuales and linea.startswith("    ") and not linea.strip().startswith("CONSTRAINT"):
            actuales[-1]["def"] += " " + sin_coment.strip()
            if comentario.strip() and not actuales[-1]["comentario"]:
                actuales[-1]["comentario"] = comentario.strip()
    return actuales


def describir(tabla, col, pk_compuesta, fks_extra):
    d = col["def"].rstrip(",")
    tipo = re.match(r"[A-Z]+(?:\s*\([^)]*\))?", d).group(0).replace(" ", "")
    reglas = []
    if "PRIMARY KEY" in d or col["nombre"] in pk_compuesta:
        reglas.append("PK")
    ref = re.search(r"REFERENCES (\w+)", d) or (fks_extra.get((tabla, col["nombre"])) and
                                                re.search(r"(\w+)", fks_extra[(tabla, col["nombre"])]))
    if ref:
        reglas.append(f"FK → {ref.group(1)}")
    if "NOT NULL" in d and "PRIMARY KEY" not in d:
        reglas.append("obligatorio")
    if re.search(r"\bUNIQUE\b", d):
        reglas.append("único")
    if "GENERATED" in d:
        reglas.append("calculado")
    defecto = re.search(r"DEFAULT ('[^']*'|[^\s,]+(?:\(\))?)", d)
    if defecto and "gen_random_uuid" not in defecto.group(1):
        reglas.append(f"por omisión {defecto.group(1)}")
    valores = re.search(r"IN \(([^)]*)\)", d)
    if valores:
        reglas.append("valores: " + valores.group(1).replace("'", ""))
    descripcion = DESCRIPCIONES.get(f"{tabla}.{col['nombre']}") or DESCRIPCIONES.get(col["nombre"]) or col["comentario"]
    if not descripcion and ref:
        descripcion = f"Referencia a {ref.group(1)}."
    if col["nombre"] == "id" and "SMALLINT" in tipo:
        descripcion = "Identificador del catálogo."
    return tipo, ", ".join(reglas), descripcion or ""


def main():
    sql = ESQUEMA.read_text(encoding="utf-8")
    fks_extra = {(m.group(1), m.group(2)): m.group(3) for m in re.finditer(
        r"ALTER TABLE (\w+)\s+ADD CONSTRAINT \w+ FOREIGN KEY \((\w+)\) REFERENCES (\w+)", sql)}
    todas = dict(tablas(sql))
    salida = ["% Generado por generar_diccionario.py a partir de db/esquema_obras.sql. No editar a mano.\n"]
    for titulo, texto, nombres in MODULOS:
        salida.append(f"\\section{{{titulo}}}\n{tex(texto)}\n")
        for nombre in nombres:
            lineas = todas.get(nombre)
            if lineas is None:
                continue
            pk = re.search(r"PRIMARY KEY \(([^)]*)\)", "\n".join(lineas))
            pk_compuesta = [c.strip() for c in pk.group(1).split(",")] if pk else []
            salida.append(f"\\tablabd{{{tex(nombre)}}}{{{tex(TABLAS.get(nombre, ''))}}}")
            salida.append("\\begin{xltabular}{\\linewidth}{@{}>{\\ttfamily\\footnotesize}p{4.9cm}>{\\ttfamily\\footnotesize}p{2.4cm}"
                          ">{\\raggedright\\arraybackslash\\footnotesize}p{3.3cm}>{\\footnotesize}X@{}}")
            salida.append("\\toprule\\normalfont\\sffamily\\bfseries\\footnotesize Campo & \\normalfont\\sffamily\\bfseries\\footnotesize Tipo & "
                          "\\sffamily\\bfseries Restricciones & \\sffamily\\bfseries Descripción\\\\\\midrule\\endfirsthead")
            salida.append("\\toprule\\normalfont\\sffamily\\bfseries\\footnotesize Campo & \\normalfont\\sffamily\\bfseries\\footnotesize Tipo & "
                          "\\sffamily\\bfseries Restricciones & \\sffamily\\bfseries Descripción\\\\\\midrule\\endhead")
            salida.append("\\bottomrule\\endfoot")
            for col in columnas(lineas):
                tipo, reglas, desc = describir(nombre, col, pk_compuesta, fks_extra)
                salida.append(f"{tex(col['nombre'])} & {tex(tipo)} & {tex(reglas)} & {tex(desc)}\\\\")
            salida.append("\\end{xltabular}\n")
    SALIDA.write_text("\n".join(salida), encoding="utf-8")
    print(f"Diccionario generado: {SALIDA.relative_to(RAIZ)} ({len(todas)} tablas)")


if __name__ == "__main__":
    main()
