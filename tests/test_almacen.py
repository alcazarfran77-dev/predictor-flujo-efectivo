"""Almacén de artefactos compartido entre el job y la API (api/almacen.py y herramientas/artefactos.py)."""
import json
import types
import threading

import pytest

from api import almacen as A
from api import main as M
from api.servicio import ServicioPronostico
from herramientas import artefactos as J

from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]


def carpeta_demo(base, texto="hola"):
    c = base / "version"
    (c / "sub").mkdir(parents=True)
    (c / "a.txt").write_text(texto)
    (c / "sub" / "b.bin").write_bytes(b"\x00\x01" * 100)
    return c


def test_publicar_descargar_y_revertir(tmp_path):
    alm = A.AlmacenLocal(tmp_path / "bucket")
    v1 = A.publicar(alm, carpeta_demo(tmp_path / "1", "uno"), {"huella_series": "a" * 64, "datos_hasta": "2026-09-30"})
    v2 = A.publicar(alm, carpeta_demo(tmp_path / "2", "dos"), {"huella_series": "b" * 64, "datos_hasta": "2026-10-01"})
    assert A.vigente(alm)["version"] == v2 and A.versiones(alm) == sorted([v1, v2])
    carpeta, man = A.descargar(alm, v2, tmp_path / "local")
    assert (carpeta / "a.txt").read_text() == "dos" and man["meta"]["datos_hasta"] == "2026-10-01"
    A.revertir(alm, v1)
    assert A.vigente(alm)["version"] == v1 and A.vigente(alm)["motivo"] == "reversión"


def test_archivo_alterado_no_se_usa(tmp_path):
    alm = A.AlmacenLocal(tmp_path / "bucket")
    v = A.publicar(alm, carpeta_demo(tmp_path / "1"), {"huella_series": "c" * 64})
    alm.escribir(f"versiones/{v}/a.txt", b"alterado")
    with pytest.raises(A.ErrorAlmacen, match="SHA-256"):
        A.descargar(alm, v, tmp_path / "local")
    assert not (tmp_path / "local" / v).exists()                 # no queda una versión a medias


def test_version_sin_manifiesto_no_se_puede_apuntar(tmp_path):
    alm = A.AlmacenLocal(tmp_path / "bucket")
    alm.escribir("versiones/incompleta/a.txt", b"x")              # un job que murió antes del manifiesto
    with pytest.raises(A.ErrorAlmacen, match="manifiesto"):
        A.revertir(alm, "incompleta")


def test_podar_conserva_la_vigente(tmp_path):
    alm = A.AlmacenLocal(tmp_path / "bucket")
    vs = [A.publicar(alm, carpeta_demo(tmp_path / str(i)), {"huella_series": f"{i}" * 64}, conservar=0) for i in range(4)]
    A.revertir(alm, vs[0])
    borradas = A.podar(alm, conservar=2)
    assert vs[0] in A.versiones(alm) and borradas == [vs[1]]


def test_vigilante_solo_avisa_cambios(tmp_path):
    alm = A.AlmacenLocal(tmp_path / "bucket")
    vig = A.Vigilante(alm)
    assert vig.revisar().version is None                         # todavía no hay apuntador
    v = A.publicar(alm, carpeta_demo(tmp_path / "1"), {"huella_series": "d" * 64})
    assert vig.revisar().version == v
    vig.version_actual = v
    assert vig.revisar().version is None                         # mismo ETag: no lee el apuntador


def test_backend_s3(tmp_path, monkeypatch):
    moto = pytest.importorskip("moto")
    import boto3
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "prueba")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "prueba")
    with moto.mock_aws():
        cliente = boto3.client("s3", region_name="us-east-1")
        cliente.create_bucket(Bucket="flujo")
        alm = A.AlmacenS3("flujo", "/prod/", cliente=cliente)
        v = A.publicar(alm, carpeta_demo(tmp_path / "1"), {"huella_series": "e" * 64})
        assert A.versiones(alm) == [v] and A.vigente(alm)["version"] == v
        assert alm.etiqueta(A.APUNTADOR) and alm.etiqueta("no/existe") is None
        carpeta, _ = A.descargar(alm, v, tmp_path / "local")
        assert (carpeta / "sub" / "b.bin").read_bytes() == b"\x00\x01" * 100
        alm.borrar_prefijo(f"versiones/{v}")
        assert A.versiones(alm) == []


@pytest.fixture(scope="module")
def almacen_con_version(tmp_path_factory):
    """El job publica una versión reutilizando el backtest guardado (así la prueba no recalcula 3 minutos)."""
    raiz = tmp_path_factory.mktemp("bucket")
    url = f"file://{raiz}"
    mp = pytest.MonkeyPatch()
    mp.setenv("FLUJO_ALMACEN", url)
    assert J.main(["publicar", "--resultados", str(RAIZ / "results"), "--conservar", "5"]) == 0
    assert J.main(["publicar", "--resultados", str(RAIZ / "results")]) == 0    # mismas series: no publica otra
    yield A.abrir(url)
    mp.undo()


def test_job_publica_todo_lo_que_la_api_necesita(almacen_con_version):
    alm = almacen_con_version
    assert len(A.versiones(alm)) == 1
    man = A.leer_manifiesto(alm, A.vigente(alm)["version"])
    rutas = {a["ruta"] for a in man["archivos"]}
    assert {"movimientos.json", "results/corrida.json", "results/tablas/backtest.csv.gz", "pronosticos/indice.json"} <= rutas
    assert man["meta"]["modelos_con_error"] == [] and man["meta"]["datos_hasta"] == "2026-09-30"


def test_api_sirve_la_version_sin_calcular(almacen_con_version, tmp_path, monkeypatch):
    monkeypatch.setenv("FLUJO_ARTEFACTOS_DIR", str(tmp_path / "local"))
    monkeypatch.setenv("FLUJO_SALIDAS", str(tmp_path / "salidas"))
    alm = almacen_con_version
    v = A.vigente(alm)["version"]
    app = types.SimpleNamespace(state=types.SimpleNamespace(servicio=None, obs=None, vigilante=A.Vigilante(alm),
                                                            candado_carga=threading.Lock()))
    s = M.cargar_version(app, alm, v)
    assert app.state.servicio is s and s.artefacto["version"] == v
    assert set(s.pronosticos_cargados) == set(s.modelos)          # no ajustó ningún modelo al arrancar
    assert all(e == "listo" for e in s.estado_backtest.values()) and s.hilo is None
    p = s.pronostico("OPERATIVO_TOTAL", "ETS", 5)
    assert p["en_cache"] and len(p["dias"]) == 5


def test_api_conserva_la_version_si_la_nueva_esta_danada(almacen_con_version, tmp_path, monkeypatch):
    monkeypatch.setenv("FLUJO_ARTEFACTOS_DIR", str(tmp_path / "local"))
    alm = A.AlmacenLocal(tmp_path / "bucket2")
    origen = almacen_con_version
    v = A.vigente(origen)["version"]
    for a in A.leer_manifiesto(origen, v)["archivos"]:           # copia la versión buena a otro almacén
        alm.escribir(f"versiones/{v}/{a['ruta']}", origen.leer(f"versiones/{v}/{a['ruta']}"))
    alm.escribir(f"versiones/{v}/manifiesto.json", origen.leer(f"versiones/{v}/manifiesto.json"))
    A.apuntar(alm, v)
    eventos = []
    obs = types.SimpleNamespace(evento=lambda *a, **k: eventos.append(a[0]))
    app = types.SimpleNamespace(state=types.SimpleNamespace(servicio=None, obs=obs, vigilante=A.Vigilante(alm),
                                                            candado_carga=threading.Lock()))
    assert M.revisar_ahora(app, alm, app.state.vigilante) == v
    buena = app.state.servicio
    man = json.loads(alm.leer(f"versiones/{v}/manifiesto.json"))  # versión nueva con un archivo alterado
    man["version"] = "20991231T000000Z-dañada"
    for a in man["archivos"]:
        alm.escribir(f"versiones/{man['version']}/{a['ruta']}", alm.leer(f"versiones/{v}/{a['ruta']}"))
    alm.escribir(f"versiones/{man['version']}/movimientos.json", b'{"movimientos": []}')
    alm.escribir(f"versiones/{man['version']}/manifiesto.json", json.dumps(man).encode())
    A.apuntar(alm, man["version"])
    assert M.revisar_ahora(app, alm, app.state.vigilante) is None
    assert app.state.servicio is buena and "artefacto_rechazado" in eventos
    assert "SHA-256" in app.state.vigilante.ultimo_error


def test_salud_sin_almacen_no_reporta_artefacto(cliente):
    assert cliente.get("/api/v1/salud").json()["artefacto"] is None
