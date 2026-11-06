# Documento de Especificación (LaTeX)

Versión en LaTeX del documento de especificación del Sistema de Obras, actualizada al sistema implementado.

- `especificacion.tex`: documento principal (portada, estilo y macros).
- `secciones/`: un archivo por capítulo. `diccionario.tex` se **genera**; no se edita a mano.
- `generar_diccionario.py`: genera el diccionario de datos desde `db/esquema_obras.sql`.
- `figuras/`: logotipos de la portada. Los diagramas están hechos con TikZ dentro del documento.

## Compilar

```bash
cd Doc/latex
make            # genera el diccionario y compila especificacion.pdf
make limpiar    # borra archivos auxiliares
```

Requiere una distribución TeX con `latexmk` (TeX Live). En Overleaf: sube la carpeta y compila `especificacion.tex`
con pdfLaTeX (el diccionario ya generado va incluido).

Para agregar o cambiar un caso de uso, usa la macro `\casouso{ID}{Actores}{Nombre}{Descripción}{Flujo}{Alternos}{Pre}{Post}{RF}`
de `especificacion.tex`; los flujos se escriben con `\item`.
