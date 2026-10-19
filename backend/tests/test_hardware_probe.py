from app.llm.hardware_probe import HardwareProfile, recommend_model


def test_small_vram_gpu_recommends_small_model_regardless_of_system_ram():
    # A small GPU must not be masked by abundant system RAM (e.g. 4GB VRAM
    # alongside 24GB RAM).
    profile = HardwareProfile(total_ram_gb=24.0, gpu_name="NVIDIA GeForce GTX 1050", vram_gb=4.0)
    rec = recommend_model(profile)
    assert rec.tier == "low-end"
    assert rec.model == "qwen3:4b"


def test_high_vram_gpu_recommends_large_model():
    profile = HardwareProfile(total_ram_gb=32.0, gpu_name="NVIDIA RTX 4090", vram_gb=24.0)
    rec = recommend_model(profile)
    assert rec.tier == "high-end"


def test_mid_vram_gpu_recommends_mid_model():
    profile = HardwareProfile(total_ram_gb=32.0, gpu_name="NVIDIA RTX 4070", vram_gb=8.0)
    rec = recommend_model(profile)
    assert rec.tier == "mid-range"
    assert rec.model == "qwen3:8b"


def test_no_gpu_falls_back_to_ram_based_tiering():
    profile = HardwareProfile(total_ram_gb=8.0, gpu_name=None, vram_gb=None)
    rec = recommend_model(profile)
    assert rec.tier == "low-end"

    profile_beefy_cpu_only = HardwareProfile(total_ram_gb=64.0, gpu_name=None, vram_gb=None)
    rec2 = recommend_model(profile_beefy_cpu_only)
    assert rec2.tier == "mid-range"
