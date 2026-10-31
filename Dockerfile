# Imagen del backend y el dashboard del predictor de flujo de efectivo.
#
#   docker build -t predictor-flujo-efectivo .
#   docker run --rm -p 8000:8000 predictor-flujo-efectivo
#   -> dashboard en http://localhost:8000/ y contrato de la API en http://localhost:8000/docs
#
# Sirve los modelos estadísticos (ETS, SARIMAX y el ingenuo). Chronos necesita GPU y se corre en el cuaderno.
# Las instrucciones completas están en la sección "Ejecutar con Docker" del README.

ARG IMAGEN_PYTHON=python:3.12-slim
FROM ${IMAGEN_PYTHON}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Primero las dependencias: esta capa se reutiliza mientras no cambie requirements-api.txt
COPY requirements-api.txt .
RUN pip install -r requirements-api.txt

# Código, datos de entrada, backtest guardado y pruebas (.dockerignore deja fuera lo que no hace falta)
COPY . .

# Usuario sin privilegios con UID 1000 (el mismo del usuario habitual en Linux y en Codespaces, para que pueda
# escribir en una carpeta montada). salidas/ y api_cache/ son las únicas carpetas donde escribe la API.
RUN if ! getent passwd 1000 > /dev/null; then useradd --create-home --uid 1000 --user-group flujo; fi \
    && mkdir -p salidas api_cache \
    && chown -R 1000:1000 salidas api_cache
USER 1000

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/salud', timeout=4)"

CMD ["python", "-m", "uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
