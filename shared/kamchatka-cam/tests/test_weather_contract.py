import importlib.util
from io import BytesIO
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

from fastapi.testclient import TestClient
from PIL import Image

from fixtures.prepare_demo import build_manifest
from monitoring.scene import Scene


def test_weather_http_contract_without_gpu(monkeypatch):
    torch = ModuleType("torch")
    torch.cuda = SimpleNamespace(OutOfMemoryError=MemoryError)
    transformers = ModuleType("transformers")
    for name in ("AutoProcessor", "BitsAndBytesConfig", "Qwen2_5_VLForConditionalGeneration"):
        setattr(transformers, name, object)
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.setitem(sys.modules, "transformers", transformers)
    path = Path(__file__).resolve().parents[1] / "cameras" / "weather_vlm_api.py"
    spec = importlib.util.spec_from_file_location("weather_contract_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    image = BytesIO()
    Image.new("RGB", (80, 60)).save(image, "JPEG")
    part = ("frame.jpg", image.getvalue(), "image/jpeg")

    def model(request, frames, prompt, max_new_tokens):
        assert frames[0].size == (80, 60)
        if prompt == module.SCENE_PROMPT:
            return json.dumps(build_manifest()["frames"][2]["scene"])
        return '{"falling_snow": "yes", "evidence": "Dense streaks in the image."}'

    monkeypatch.setattr(module, "run_model", model)
    # The test avoids model startup and exercises the real HTTP parsing and response path.
    with_client = TestClient(module.app)
    response = with_client.post("/v1/scene", files={"image": part})
    assert response.status_code == 200
    assert Scene.model_validate(response.json()).intensity == "heavy"
    assert with_client.post("/v1/snow", files={"image": part}).json()["falling_snow"] == "yes"
    assert with_client.post("/v1/snow", files=[("images", part), ("images", part)]).json()["frames"] == 2
    assert with_client.post("/v1/snow", files=[("image", part), ("images", part)]).status_code == 422
    assert with_client.post("/v1/scene", files={"image": ("bad.jpg", b"bad", "image/jpeg")}).status_code == 422
    monkeypatch.setattr(module, "run_model", lambda *_: '{"falling_snow": "yes"}')
    invalid = with_client.post("/v1/scene", files={"image": part})
    assert not Scene.model_validate(invalid.json()).format_valid
