"""Hardware-aware local model recommendation.

Probes system RAM and, when an NVIDIA GPU is present, VRAM via
`nvidia-smi` (no extra dependency), then suggests an Ollama model tier.
Detection failures never block the app; they fall back to the smallest
model rather than assuming capable hardware.

An 8B-class model needs roughly 5-6GB of VRAM at Q4 quantization to run
without spilling onto the CPU.
"""

import shutil
import subprocess
from dataclasses import dataclass

import psutil


@dataclass
class HardwareProfile:
    total_ram_gb: float
    gpu_name: str | None
    vram_gb: float | None


@dataclass
class ModelRecommendation:
    model: str
    tier: str
    reason: str


def probe_hardware() -> HardwareProfile:
    total_ram_gb = psutil.virtual_memory().total / (1024**3)
    gpu_name, vram_gb = _probe_nvidia_gpu()
    return HardwareProfile(total_ram_gb=round(total_ram_gb, 1), gpu_name=gpu_name, vram_gb=vram_gb)


def _probe_nvidia_gpu() -> tuple[str | None, float | None]:
    if shutil.which("nvidia-smi") is None:
        return None, None
    try:
        output = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5, check=True,
        ).stdout.strip()
        name, vram_mib = output.split(",")
        return name.strip(), round(float(vram_mib.strip()) / 1024, 1)
    except Exception:
        return None, None  # no NVIDIA GPU, driver not installed, or nvidia-smi behaved unexpectedly


def recommend_model(profile: HardwareProfile | None = None) -> ModelRecommendation:
    """Tiering is driven by VRAM when a GPU is present, not total system RAM.

    A small GPU (e.g. 4GB VRAM alongside 24GB system RAM) still forces Ollama
    to split the model across GPU and CPU, which is slow and runs hot, and
    extra system RAM does not help. RAM drives the tier only when there is no
    usable GPU and inference is CPU-bound.
    """
    profile = profile or probe_hardware()
    has_gpu = profile.vram_gb is not None
    vram = profile.vram_gb or 0

    if has_gpu:
        if vram >= 12:
            return ModelRecommendation(
                model="qwen3:30b-a3b", tier="high-end",
                reason=f"{vram:.0f}GB VRAM: a MoE model this size fits comfortably with headroom.",
            )
        if vram >= 6:
            return ModelRecommendation(
                model="qwen3:8b", tier="mid-range",
                reason=f"{vram:.0f}GB VRAM is enough to keep an 8B model at Q4 mostly resident on GPU.",
            )
        return ModelRecommendation(
            model="qwen3:4b", tier="low-end",
            reason=(
                f"Only {vram:.0f}GB VRAM detected ({profile.gpu_name}); an 8B model at Q4 needs ~5-6GB and "
                "would spill across GPU/CPU, which is slow and runs hot (observed directly during development: "
                "a GTX 1050 4GB running qwen3:8b split 62%/38% CPU/GPU and hit 93°C). A 4B model fits fully."
            ),
        )

    if profile.total_ram_gb >= 32:
        return ModelRecommendation(
            model="qwen3:8b", tier="mid-range",
            reason=f"No GPU detected; {profile.total_ram_gb:.0f}GB system RAM is enough for CPU-only 8B inference.",
        )
    return ModelRecommendation(
        model="qwen3:4b", tier="low-end",
        reason=f"No GPU detected and only {profile.total_ram_gb:.0f}GB system RAM; a smaller model stays responsive.",
    )
