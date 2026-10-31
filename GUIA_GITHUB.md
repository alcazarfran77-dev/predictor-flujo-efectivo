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

La carga desde el navegador acepta hasta 100 archivos a la vez y el proyecto tiene 144, así que se sube en dos partes.

**Primera carga: todo menos `docs` (87 archivos).**

1. En la página del repositorio vacío, haz clic en el enlace "uploading an existing file".
2. En la carpeta `predictor-flujo-efectivo`, selecciona todo (Ctrl + A) y quita `docs` de la selección (Ctrl + clic sobre ella).
3. Arrastra la selección a la ventana del navegador y espera a que termine la lista de archivos.
4. Abajo, en "Commit changes", escribe: `Predictor de flujo de efectivo`. Deja "Commit directly to the main branch" y haz clic en "Commit changes".

**Segunda carga: la carpeta `docs` (57 archivos).**

1. En el repositorio, "Add file", "Upload files".
2. Arrastra la carpeta `docs` completa (la carpeta, no su contenido).
3. Mensaje: `Página publicada`. "Commit changes".

Al terminar, la página del repositorio debe mostrar las carpetas `.github`, `api`, `data`, `docs`, `frontend`, `notebooks`, `results`, `salidas`, `src` y `tests`, y el README con su contenido abajo. Si falta `.github`, créala a mano: "Add file", "Create new file", escribe como nombre `.github/workflows/pruebas.yml` (las diagonales crean las carpetas), pega el contenido del archivo que está en tu carpeta y haz clic en "Commit changes". Repite lo mismo con `.github/workflows/docker.yml`.

## 4. Publicar la página

1. En el repositorio, "Settings" (arriba a la derecha), y en el menú izquierdo, "Pages".
2. En "Build and deployment", "Source": "Deploy from a branch".
3. En "Branch" elige `main` y en la carpeta, `/docs`. "Save".
4. Espera de uno a tres minutos y recarga. Arriba aparece "Your site is live at" con la dirección `https://tu-usuario.github.io/predictor-flujo-efectivo/`.

La página es el dashboard completo con el pronóstico, la alerta de déficit, la confianza del modelo y el botón para descargar el reporte JSON. Arriba a la derecha dice "Versión publicada, sin servidor": lee las respuestas de la API que se guardaron en `docs/`, porque GitHub Pages no corre programas.

## 5. Poner tu usuario en los enlaces

El README y el cuaderno de Colab traen **&lt;usuario&gt;** en lugar de tu usuario de GitHub, en los botones de Colab, de la página y de las pruebas. Para reemplazarlo de una vez:

1. En la página del repositorio, presiona la tecla punto (`.`). Se abre github.dev, un editor dentro del navegador.
2. Presiona Ctrl + Mayús + H (buscar y reemplazar en todos los archivos).
3. Busca **&lt;usuario&gt;** y escribe tu usuario de GitHub en el campo de reemplazo, sin los signos &lt; &gt;. Haz clic en el botón "Replace All" (el ícono a la derecha del campo) y confirma.
4. Abre "Source Control" (Ctrl + Mayús + G), escribe el mensaje `Enlaces con mi usuario` y haz clic en "Commit & Push".
5. Vuelve a github.com: los botones del README ya llevan a tu página, a tus pruebas y a tu cuaderno en Colab.

## 6. Revisar las pruebas

Con cada cambio, GitHub corre las 71 pruebas del proyecto (validación de datos, API, página publicada y navegador) con el flujo de `.github/workflows/pruebas.yml`, y con `.github/workflows/docker.yml` construye la imagen de Docker, corre las pruebas dentro del contenedor y comprueba que la API arranca. Se ven en la pestaña "Actions": la primera vez tarda unos cinco minutos. La palomita verde junto a cada commit indica que pasaron; si aparece una cruz roja, al abrir la corrida se ve cuál prueba falló.

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
git remote add origin https://github.com/tu-usuario/predictor-flujo-efectivo.git
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
| El README no muestra los botones con tu usuario | Falta el paso 5. |
| GitHub no deja subir un archivo | El límite por archivo es de 25 MB; el más grande del proyecto pesa 2 MB. |
