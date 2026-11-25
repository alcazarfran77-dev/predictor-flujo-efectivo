"""Almacén de artefactos que comparten el job diario y la API.

Render no deja montar un disco persistente en un Cron Job y un disco solo lo ve el servicio que lo tiene, así que el
job no puede dejarle archivos a la API en un disco. El job publica cada corrida como una versión inmutable en un
almacén de objetos compatible con S3 (Cloudflare R2, AWS S3 o MinIO) y la API la descarga al arrancar y cada vez que
cambia el apuntador `actual.json`.

Estructura dentro del prefijo:

    entrada/movimientos.json                  datos que deja el proceso de tesorería (los lee el job)
    versiones/<id>/movimientos.json           copia exacta de los datos usados en la corrida
    versiones/<id>/results/corrida.json       huella de las series y parámetros (la API la usa para validar)
    versiones/<id>/results/tablas/backtest.csv.gz
    versiones/<id>/pronosticos/indice.json    pronóstico de cada modelo, calculado en el job
    versiones/<id>/pronosticos/<modelo>.csv.gz
    versiones/<id>/manifiesto.json            tamaño y SHA-256 de cada archivo; se escribe después de los archivos
    actual.json                               apuntador a la versión vigente; se escribe al final

Reglas:
- Una versión no se modifica nunca. Publicar = subir archivos, subir el manifiesto y, al último, cambiar el apuntador.
  Una corrida que falla a la mitad deja archivos huérfanos, pero la API sigue con la versión anterior.
- La API verifica el SHA-256 de cada archivo antes de usarlo; si no coincide, conserva la versión que tiene.
- Revertir = apuntar `actual.json` a una versión anterior. Todas las instancias de la API la toman en su siguiente revisión.

Configuración:
    FLUJO_ALMACEN          s3://bucket/prefijo  o  file:///ruta/local  (sin valor: la API trabaja como antes, sin almacén)
    FLUJO_S3_ENDPOINT      endpoint S3 del proveedor (R2: https://<cuenta>.r2.cloudflarestorage.com)
    AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_REGION   credenciales del token del bucket (R2 usa la región "auto")
"""
import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

APUNTADOR = "actual.json"
MANIFIESTO = "manifiesto.json"
ENTRADA = "entrada/movimientos.json"
VERSIONES = "versiones"


class ErrorAlmacen(Exception):
    """Falla al leer, escribir o verificar un artefacto."""


def sha256_bytes(datos):
    return hashlib.sha256(datos).hexdigest()


def sha256_archivo(ruta):
    h = hashlib.sha256()
    with open(ruta, "rb") as f:
        for bloque in iter(lambda: f.read(1 << 20), b""):
            h.update(bloque)
    return h.hexdigest()


def ahora_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------------------------------- backends
class AlmacenLocal:
    """Carpeta local con la misma interfaz que S3. Sirve para pruebas, Codespaces y desarrollo."""

    def __init__(self, raiz):
        self.raiz = Path(raiz)
        self.descripcion = f"file://{self.raiz}"

    def _ruta(self, clave):
        return self.raiz / clave

    def leer(self, clave):
        try:
            return self._ruta(clave).read_bytes()
        except FileNotFoundError:
            return None

    def escribir(self, clave, datos):
        ruta = self._ruta(clave)
        ruta.parent.mkdir(parents=True, exist_ok=True)
        temporal = ruta.with_name(ruta.name + ".tmp")
        temporal.write_bytes(datos)
        os.replace(temporal, ruta)                       # el lector ve el archivo viejo o el nuevo, nunca uno a medias

    def subir(self, local, clave):
        self.escribir(clave, Path(local).read_bytes())

    def bajar(self, clave, local):
        datos = self.leer(clave)
        if datos is None:
            raise ErrorAlmacen(f"No existe {clave} en {self.descripcion}")
        Path(local).parent.mkdir(parents=True, exist_ok=True)
        Path(local).write_bytes(datos)

    def etiqueta(self, clave):
        datos = self.leer(clave)
        return None if datos is None else sha256_bytes(datos)

    def subcarpetas(self, prefijo):
        base = self._ruta(prefijo)
        return sorted(p.name for p in base.iterdir() if p.is_dir()) if base.exists() else []

    def borrar_prefijo(self, prefijo):
        shutil.rmtree(self._ruta(prefijo), ignore_errors=True)


class AlmacenS3:
    """Bucket compatible con S3. Las credenciales salen de las variables estándar de AWS."""

    def __init__(self, bucket, prefijo="", endpoint=None, region=None, cliente=None):
        import boto3
        from botocore.config import Config as ConfigBoto

        self.bucket, self.prefijo = bucket, prefijo.strip("/")
        self.descripcion = f"s3://{bucket}/{self.prefijo}"
        self.cliente = cliente or boto3.client(
            "s3", endpoint_url=endpoint or os.environ.get("FLUJO_S3_ENDPOINT") or None,
            region_name=region or os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "auto",
            config=ConfigBoto(retries={"max_attempts": 4, "mode": "standard"}, connect_timeout=5, read_timeout=30))

    def _clave(self, clave):
        return f"{self.prefijo}/{clave}" if self.prefijo else clave

    @staticmethod
    def _no_existe(error):
        return error.response.get("Error", {}).get("Code") in ("NoSuchKey", "404", "NotFound")

    def leer(self, clave):
        from botocore.exceptions import ClientError
        try:
            return self.cliente.get_object(Bucket=self.bucket, Key=self._clave(clave))["Body"].read()
        except ClientError as e:
            if self._no_existe(e):
                return None
            raise ErrorAlmacen(f"No se pudo leer {clave}: {e}") from e

    def escribir(self, clave, datos):
        tipo = "application/json" if clave.endswith(".json") else "application/octet-stream"
        self.cliente.put_object(Bucket=self.bucket, Key=self._clave(clave), Body=datos, ContentType=tipo)

    def subir(self, local, clave):
        self.cliente.upload_file(str(local), self.bucket, self._clave(clave))

    def bajar(self, clave, local):
        from botocore.exceptions import ClientError
        Path(local).parent.mkdir(parents=True, exist_ok=True)
        try:
            self.cliente.download_file(self.bucket, self._clave(clave), str(local))
        except ClientError as e:
            raise ErrorAlmacen(f"No se pudo bajar {clave}: {e}") from e

    def etiqueta(self, clave):
        """ETag del objeto (una petición HEAD); None si no existe. Sirve para revisar cambios sin bajar el archivo."""
        from botocore.exceptions import ClientError
        try:
            return self.cliente.head_object(Bucket=self.bucket, Key=self._clave(clave))["ETag"]
        except ClientError as e:
            if self._no_existe(e):
                return None
            raise ErrorAlmacen(f"No se pudo revisar {clave}: {e}") from e

    def subcarpetas(self, prefijo):
        base = self._clave(prefijo.rstrip("/") + "/")
        nombres, pag = [], self.cliente.get_paginator("list_objects_v2")
        for pagina in pag.paginate(Bucket=self.bucket, Prefix=base, Delimiter="/"):
            nombres += [p["Prefix"][len(base):].rstrip("/") for p in pagina.get("CommonPrefixes", [])]
        return sorted(nombres)

    def borrar_prefijo(self, prefijo):
        base = self._clave(prefijo.rstrip("/") + "/")
        pag = self.cliente.get_paginator("list_objects_v2")
        for pagina in pag.paginate(Bucket=self.bucket, Prefix=base):
            claves = [{"Key": o["Key"]} for o in pagina.get("Contents", [])]
            if claves:
                self.cliente.delete_objects(Bucket=self.bucket, Delete={"Objects": claves, "Quiet": True})


def abrir(url=None):
    """Abre el almacén de FLUJO_ALMACEN (o de la URL dada). Devuelve None si no hay almacén configurado."""
    url = url or os.environ.get("FLUJO_ALMACEN")
    if not url:
        return None
    u = urlparse(url)
    if u.scheme == "file":
        return AlmacenLocal(u.path)
    if u.scheme == "s3":
        return AlmacenS3(u.netloc, u.path)
    raise ValueError(f"FLUJO_ALMACEN debe empezar con s3:// o file:// (recibí {url!r})")


# ---------------------------------------------------------------------------------------------------- versiones
def nuevo_id(huella_series):
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + huella_series[:8]


def publicar(almacen, carpeta, meta, conservar=30):
    """Sube una carpeta como versión nueva y mueve el apuntador. Devuelve el id de la versión."""
    carpeta = Path(carpeta)
    version = nuevo_id(meta.get("huella_series", "sin-huella"))
    archivos = []
    for ruta in sorted(p for p in carpeta.rglob("*") if p.is_file()):
        rel = ruta.relative_to(carpeta).as_posix()
        almacen.subir(ruta, f"{VERSIONES}/{version}/{rel}")
        archivos.append({"ruta": rel, "bytes": ruta.stat().st_size, "sha256": sha256_archivo(ruta)})
    manifiesto = {"version": version, "creado": ahora_iso(), "archivos": archivos, "meta": meta}
    almacen.escribir(f"{VERSIONES}/{version}/{MANIFIESTO}", json.dumps(manifiesto, ensure_ascii=False, indent=1).encode())
    apuntar(almacen, version, manifiesto)
    if conservar:
        podar(almacen, conservar)
    return version


def apuntar(almacen, version, manifiesto=None, motivo="publicación"):
    """Escribe actual.json (un solo PUT: los lectores ven el apuntador viejo o el nuevo)."""
    manifiesto = manifiesto or leer_manifiesto(almacen, version)
    apuntador = {"version": version, "apuntado": ahora_iso(), "motivo": motivo,
                 "huella_series": manifiesto["meta"].get("huella_series"),
                 "datos_hasta": manifiesto["meta"].get("datos_hasta"),
                 "bytes": sum(a["bytes"] for a in manifiesto["archivos"])}
    almacen.escribir(APUNTADOR, json.dumps(apuntador, ensure_ascii=False, indent=1).encode())
    return apuntador


def leer_manifiesto(almacen, version):
    datos = almacen.leer(f"{VERSIONES}/{version}/{MANIFIESTO}")
    if datos is None:
        raise ErrorAlmacen(f"La versión {version} no tiene manifiesto: no está completa o no existe")
    return json.loads(datos)


def vigente(almacen):
    """Contenido de actual.json o None si todavía no hay versiones."""
    datos = almacen.leer(APUNTADOR)
    return None if datos is None else json.loads(datos)


def versiones(almacen):
    return almacen.subcarpetas(VERSIONES)


def revertir(almacen, version):
    """Vuelve a una versión anterior: solo mueve el apuntador. Las instancias de la API la cargan al revisar."""
    return apuntar(almacen, version, motivo="reversión")


def podar(almacen, conservar=30):
    """Borra las versiones más viejas; nunca la vigente."""
    actual = (vigente(almacen) or {}).get("version")
    todas = versiones(almacen)
    borradas = [v for v in todas[:-conservar] if v != actual] if len(todas) > conservar else []
    for v in borradas:
        almacen.borrar_prefijo(f"{VERSIONES}/{v}")
    return borradas


def descargar(almacen, version, destino):
    """Baja una versión completa a destino/<version>/ y verifica cada SHA-256. Devuelve (carpeta, manifiesto)."""
    destino = Path(destino)
    final = destino / version
    if (final / MANIFIESTO).exists():
        return final, json.loads((final / MANIFIESTO).read_text(encoding="utf-8"))
    manifiesto = leer_manifiesto(almacen, version)
    destino.mkdir(parents=True, exist_ok=True)
    parcial = Path(tempfile.mkdtemp(prefix=f"{version}.", dir=destino))
    try:
        for a in manifiesto["archivos"]:
            local = parcial / a["ruta"]
            almacen.bajar(f"{VERSIONES}/{version}/{a['ruta']}", local)
            if sha256_archivo(local) != a["sha256"]:
                raise ErrorAlmacen(f"{a['ruta']} de la versión {version} no coincide con su SHA-256")
        (parcial / MANIFIESTO).write_text(json.dumps(manifiesto, ensure_ascii=False), encoding="utf-8")
        os.replace(parcial, final)                       # la carpeta aparece completa o no aparece
    except Exception:
        shutil.rmtree(parcial, ignore_errors=True)
        raise
    return final, manifiesto


@dataclass
class Revision:
    """Resultado de revisar el apuntador: None en `version` si no cambió."""
    version: str = None
    apuntador: dict = None
    etiqueta: str = None


class Vigilante:
    """Revisa el apuntador con una petición HEAD y solo lo lee completo cuando su ETag cambia."""

    def __init__(self, almacen, version_actual=None):
        self.almacen = almacen
        self.version_actual = version_actual
        self.etiqueta = None
        self.ultima_revision = None
        self.ultimo_error = None

    def revisar(self):
        self.ultima_revision = ahora_iso()
        etiqueta = self.almacen.etiqueta(APUNTADOR)
        if etiqueta is None or etiqueta == self.etiqueta:
            return Revision()
        apuntador = vigente(self.almacen)
        self.etiqueta = etiqueta
        if not apuntador or apuntador["version"] == self.version_actual:
            return Revision()
        return Revision(apuntador["version"], apuntador, etiqueta)
