"""Dispositivo, semillas, sincronización de GPU y versiones de paquetes (secciones 1 y 2 del cuaderno)."""
import importlib.metadata as metadata
import random

import numpy as np

PAQUETES = ["chronos-forecasting", "transformers", "accelerate", "torch", "huggingface_hub",
            "pandas", "numpy", "statsmodels", "scikit-learn", "matplotlib", "openpyxl"]


def detectar_dispositivo(forzar_cpu=False):
    """Devuelve (dispositivo, dtype). En T4 (capacidad 7.5) usa float32; en L4/A100, bfloat16."""
    try:
        import torch
    except ImportError:
        return "cpu", None
    if torch.cuda.is_available() and not forzar_cpu:
        capacidad = torch.cuda.get_device_capability(0)
        torch.backends.cuda.matmul.allow_tf32 = True
        torch.backends.cudnn.allow_tf32 = True
        return "cuda", (torch.bfloat16 if capacidad[0] >= 8 else torch.float32)
    return "cpu", torch.float32


def descripcion_gpu():
    try:
        import torch
        if torch.cuda.is_available():
            p = torch.cuda.get_device_properties(0)
            c = torch.cuda.get_device_capability(0)
            return f"{p.name} | VRAM {p.total_memory / 1024**3:.1f} GB | compute capability {c[0]}.{c[1]}"
    except ImportError:
        pass
    return "sin GPU"


def fijar_semillas(semilla, dispositivo="cpu"):
    random.seed(semilla)
    np.random.seed(semilla)
    try:
        import torch
        torch.manual_seed(semilla)
        if dispositivo == "cuda":
            torch.cuda.manual_seed_all(semilla)
    except ImportError:
        pass


def crear_sincronizador(dispositivo):
    """Función que espera a que terminen los kernels de GPU, para que el cronómetro mida tiempo real."""
    if dispositivo != "cuda":
        return lambda: None
    import torch
    return torch.cuda.synchronize


def versiones():
    salida = {}
    for paquete in PAQUETES:
        try:
            salida[paquete] = metadata.version(paquete)
        except metadata.PackageNotFoundError:
            salida[paquete] = "no instalado"
    return salida
