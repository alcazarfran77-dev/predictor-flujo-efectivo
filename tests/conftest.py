"""Fixtures compartidas: un servicio real (JSON de data/ y backtest de results/) y un cliente HTTP de la API.
Los reportes JSON de las pruebas se escriben en una carpeta temporal, no en salidas/."""
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "src"))

from fastapi.testclient import TestClient  # noqa: E402

from api.main import app  # noqa: E402
from api.servicio import ServicioPronostico  # noqa: E402


@pytest.fixture(scope="session")
def dir_salidas(tmp_path_factory):
    return tmp_path_factory.mktemp("salidas")


@pytest.fixture(scope="session")
def servicio(dir_salidas):
    return ServicioPronostico(dir_salidas=dir_salidas).iniciar()


@pytest.fixture(scope="session")
def cliente(servicio):
    app.state.servicio = servicio
    with TestClient(app) as c:
        yield c
