# Compila el documento de especificación.  Uso: make   |   make limpiar
especificacion.pdf: especificacion.tex secciones/*.tex
	python generar_diccionario.py
	latexmk -pdf -interaction=nonstopmode -halt-on-error especificacion.tex

limpiar:
	latexmk -C
