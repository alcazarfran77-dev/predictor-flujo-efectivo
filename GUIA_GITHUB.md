# Cómo subir el proyecto a GitHub y publicar el dashboard

Todo se hace desde el navegador, sin instalar nada. Solo se necesita una cuenta de GitHub. Los botones de GitHub están en inglés, así que aquí aparecen con su nombre original entre comillas.

Antes de empezar, una advertencia: con una cuenta gratuita, GitHub Pages solo publica repositorios públicos. Eso hace públicos los datos de `data/`, que están anonimizados (sin nombres, cuentas ni CLABE), pero conservan importes y fechas, y una semana de movimientos es real. Conviene confirmar con la empresa que se pueden mostrar así.

## 1. Preparar los archivos

1. Descomprime `predictor-flujo-efectivo.zip` (en Windows: clic derecho, "Extraer todo").
2. Abre la carpeta `predictor-flujo-efectivo`. Debes ver `README.md`, `GUIA_GITHUB.md`, `Dockerfile`, `compose.yaml`, las carpetas `api`, `data`, `docs`, `frontend`, `notebooks`, `results`, `salidas`, `src` y `tests`, y además `.github`, `.gitignore`, `.gitattributes` y `.dockerignore`. En Mac estos cuatro últimos están ocultos: se ven con Cmd + Mayús + punto.

## 2. Crear el repositorio

1. En [github.com](https://github.com), botón "+" de arriba a la derecha, "New repository".
2. "Repository name": `predictor-flujo-efectivo`. Con otro nombre también funciona, pero cambia la dirección de la página.
3. "Description" (opcional): Predictor de flujo de efectivo con Chronos-2, ETS y SARIMAX, con API y dashboard.
4. Marca "Public".
5. No marques "Add a README file" ni elijas `.gitignore` o licencia: el repositorio tiene que nacer vacío.
6. "Create repository".

## 3. Subir los archivos

La carga desde el navegador acepta hasta 100 archivos a la vez y no acepta archivos ocultos (los que empiezan con punto): los marca con "This file is hidden." y los omite. Por eso se sube en dos partes y después se crean a mano los cinco archivos ocultos.

**Primera carga: todo menos `docs` (82 archivos visibles).**

1. En la página del repositorio vacío, haz clic en el enlace "uploading an existing file".
2. En la carpeta `predictor-flujo-efectivo`, selecciona todo (Ctrl + A) y quita `docs` de la selección (Ctrl + clic sobre ella).
3. Arrastra la selección a la ventana del navegador y espera a que termine la lista. Los avisos "This file is hidden." de `.github`, `.gitignore`, `.gitattributes` y `.dockerignore` son normales: esos se crean en el paso siguiente.
4. Abajo, en "Commit changes", escribe: `Predictor de flujo de efectivo`. Deja "Commit directly to the main branch" y haz clic en "Commit changes".

**Segunda carga: la carpeta `docs` (56 archivos visibles).**

1. En el repositorio, "Add file", "Upload files".
2. Arrastra la carpeta `docs` completa (la carpeta, no su contenido). El aviso de `.nojekyll` también es normal; la página funciona sin ese archivo.
3. Mensaje: `Página publicada`. "Commit changes".

**Crear los archivos ocultos.** Para cada uno de los cinco archivos de abajo:

1. En la página principal del repositorio, "Add file", "Create new file".
2. En el campo del nombre escribe la ruta tal como aparece, empezando con el punto. Al escribir cada diagonal (`/`), GitHub crea la carpeta: por ejemplo, `.github/workflows/pruebas.yml` crea las carpetas `.github` y `workflows` y el archivo `pruebas.yml`.
3. Copia el contenido con el botón de copiar del bloque y pégalo en el editor.
4. "Commit changes...", y de nuevo "Commit changes" en la ventana que aparece.

Los dos primeros son los más importantes, porque son las pruebas automáticas y la revisión de Docker. Los otros tres sirven para trabajar con git y para construir la imagen de Docker sin archivos de más.

`.github/workflows/pruebas.yml`

```yaml
# Corre las pruebas en cada cambio que se sube a GitHub. El resultado aparece en la pestaña Actions
# y como una palomita (o una cruz) junto a cada commit.
name: Pruebas

on:
  push:
  pull_request:
  workflow_dispatch:

permissions:
  contents: read

jobs:
  pruebas:
    runs-on: ubuntu-latest
    timeout-minutes: 20
    steps:
      - name: Descargar el repositorio
        uses: actions/checkout@v4

      - name: Instalar Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip
          cache-dependency-path: requirements-api.txt

      - name: Instalar dependencias y Chromium
        run: |
          pip install -r requirements-api.txt playwright
          python -m playwright install --with-deps chromium

      - name: Validar el JSON de movimientos
        run: python -m api.validar data/movimientos_feban_sep2025_sep2026_anon.json

      - name: Pruebas de validación, API, página y navegador
        run: python -m pytest tests -q

      - name: Revisar que docs/ se pueda volver a generar
        run: python -m api.publicar --salida /tmp/pagina
```

`.github/workflows/docker.yml`

```yaml
# Construye la imagen de Docker con cada cambio, corre las pruebas dentro del contenedor y comprueba que la API
# arranca, responde y pasa su revisión de salud (HEALTHCHECK).
name: Docker

on:
  push:
  pull_request:
  workflow_dispatch:

permissions:
  contents: read

jobs:
  imagen:
    runs-on: ubuntu-latest
    timeout-minutes: 20
    steps:
      - name: Descargar el repositorio
        uses: actions/checkout@v4

      - name: Construir la imagen
        run: docker build -t predictor-flujo-efectivo .

      - name: Pruebas dentro del contenedor
        run: docker run --rm predictor-flujo-efectivo python -m pytest tests -q -p no:cacheprovider

      - name: Arrancar la API y consultarla
        run: |
          docker run -d --name api -p 8000:8000 predictor-flujo-efectivo
          for i in $(seq 1 45); do
            estado=$(docker inspect --format '{{.State.Health.Status}}' api)
            [ "$estado" = "healthy" ] && break
            sleep 2
          done
          echo "Estado de salud: $estado"
          test "$estado" = "healthy"
          curl -fsS -o /dev/null http://localhost:8000/
          curl -fsS "http://localhost:8000/api/v1/pronostico?serie=OPERATIVO_TOTAL&modelo=ETS" \
            | python3 -c "import json, sys; d = json.load(sys.stdin); assert len(d['dias']) == 5; print('Alerta:', d['alerta_deficit']['nivel'])"
          docker exec api ls salidas

      - name: Registro del contenedor
        if: always()
        run: docker logs api || true
```

`.gitignore`

```gitignore
# Python
__pycache__/
*.py[cod]
.venv/
venv/
.env

# Jupyter
.ipynb_checkpoints/

# Pesos descargados de Hugging Face y cachés
.cache/
hf_cache/
modelos/

# Salidas que se generan al correr en local
notebooks/resultados_predictor_flujo.xlsx
api_cache/
api.log
.pytest_cache/

# Archivos de Office abiertos
~$*.xlsx

# Exports bancarios sin anonimizar: nunca se suben
data/*FEBAN*.xlsx
!data/*_anon.xlsx
data/*feban*.json
!data/*_anon.json

# Sistema
.DS_Store
Thumbs.db
```

`.gitattributes`

```text
# Fin de línea uniforme (LF) para que los archivos no cambien al editarse en Windows
* text=auto eol=lf

# Binarios: git no intenta compararlos ni convertir fines de línea
*.png binary
*.xlsx binary
*.woff2 binary
*.gz binary
*.zip binary

# Archivos generados: GitHub los colapsa en las revisiones de cambios
docs/** linguist-generated=true
results/tablas/** linguist-generated=true
salidas/*.json linguist-generated=true
```

`.dockerignore`

```text
# Lo que no necesita la imagen: control de versiones, cuadernos, la página generada y salidas pesadas
.git
.github
.gitignore
.gitattributes
.dockerignore
Dockerfile
compose.yaml
**/__pycache__
**/*.pyc
.pytest_cache
.ipynb_checkpoints
.venv
venv
api_cache
api.log
notebooks
docs
salidas/*.json
results/figuras
results/capturas
results/resultados_predictor_flujo.xlsx
*.zip
```

Al terminar, la página del repositorio debe mostrar las carpetas `.github`, `api`, `data`, `docs`, `frontend`, `notebooks`, `results`, `salidas`, `src` y `tests`, los archivos `.dockerignore`, `.gitattributes`, `.gitignore`, `Dockerfile`, `compose.yaml` y los README, y el README con su contenido abajo. En la pestaña "Actions" ya aparecen corriendo "Pruebas" y "Docker".

## 4. Publicar la página

1. En el repositorio, "Settings" (arriba a la derecha), y en el menú izquierdo, "Pages".
2. En "Build and deployment", "Source": "Deploy from a branch".
3. En "Branch" elige `main` y en la carpeta, `/docs`. "Save".
4. Espera de uno a tres minutos y recarga. Arriba aparece "Your site is live at" con la dirección `https://alcazarfran77-dev.github.io/predictor-flujo-efectivo/`.

La página es el dashboard completo con el pronóstico, la alerta de déficit, la confianza del modelo y el botón para descargar el reporte JSON. Arriba a la derecha dice "Versión publicada, sin servidor": lee las respuestas de la API que se guardaron en `docs/`, porque GitHub Pages no corre programas.

## 5. Enlaces con tu usuario

El README, el cuaderno `notebooks/api_dashboard_colab.ipynb` y `api/publicar.py` ya traen el usuario `alcazarfran77-dev` en los botones de la página, de las pruebas, de Docker y de Colab. Si en el repositorio todavía aparece `<usuario>` en alguno de esos tres archivos, sube la versión nueva con "Add file", "Upload files": GitHub reemplaza los archivos con el mismo nombre.

## 6. Revisar las pruebas

Con cada cambio, GitHub corre las 97 pruebas del proyecto (validación de datos, API, página publicada y navegador) con el flujo de `.github/workflows/pruebas.yml`, y con `.github/workflows/docker.yml` construye la imagen de Docker, corre las pruebas dentro del contenedor y comprueba que la API arranca. Se ven en la pestaña "Actions": la primera vez tarda unos cinco minutos. La palomita verde junto a cada commit indica que pasaron; si aparece una cruz roja, al abrir la corrida se ve cuál prueba falló.

## 7. Actualizar la página cuando cambien los datos

La página muestra los datos con los que se generó y su fecha de publicación. Para actualizarla:

1. Pon el nuevo JSON de movimientos en `data/` (si viene de un Excel FEBAN, conviértelo con `python src/excel_a_json.py`).
2. Abre `notebooks/api_dashboard_colab.ipynb` en Colab y ejecuta la sección "Actualizar la página publicada": vuelve a generar `docs/` y la descarga en un zip.
3. Descomprime el zip y sube la carpeta `docs` como en la segunda carga del paso 3. GitHub reemplaza los archivos con el mismo nombre y la página se actualiza en uno o dos minutos.

## Otra forma: con git

Desde Colab, GitHub Codespaces o una computadora que ya tenga git, dentro de la carpeta del proyecto:

```bash
git init -b main
git add .
git commit -m "Predictor de flujo de efectivo"
git remote add origin https://github.com/alcazarfran77-dev/predictor-flujo-efectivo.git
git push -u origin main
```

Al hacer `push`, GitHub pide un token en lugar de la contraseña. Se crea en "Settings", "Developer settings", "Personal access tokens", "Fine-grained tokens", con permiso "Contents: Read and write" solo para este repositorio. Después se sigue desde el paso 4.

## Probar el contenedor sin instalar Docker

Con el repositorio ya en GitHub: "Code", "Codespaces", "Create codespace on main". Se abre un editor con terminal en el navegador; ahí se ejecuta `docker compose up --build` y, cuando Codespaces avisa que el puerto 8000 está listo, "Open in Browser" muestra el dashboard servido desde el contenedor. Los demás comandos están en la sección "Ejecutar con Docker" del README. Al terminar, conviene detener el codespace ("Code", "Codespaces", "Stop codespace") para no gastar las horas gratuitas del mes.

## Licencia

Sin licencia, el código se puede ver pero nadie tiene permiso para reutilizarlo. Si quieres permitirlo: "Add file", "Create new file", nombre `LICENSE`, y GitHub ofrece "Choose a license template" con las licencias más comunes.

## Si algo no funciona

| Qué pasa | Qué revisar |
|---|---|
| La página da error 404 | Espera unos minutos más. En "Settings", "Pages" debe decir `main` y `/docs`, y `docs/index.html` debe existir en el repositorio. |
| La página carga pero dice que no pudo leer un archivo | Falta algo en `docs/`: vuelve a subir la carpeta completa. |
| Aparece una cruz roja en "Actions" | Abre la corrida para ver qué prueba falló. La página se publica de todos modos; las pruebas solo avisan. |
| Los botones del README llevan a una página que no existe | En el README todavía dice `<usuario>`: sube el README nuevo (paso 5). |
| GitHub no deja subir un archivo | El límite por archivo es de 25 MB; el más grande del proyecto pesa 2 MB. |
