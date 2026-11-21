"""Serve the local Weather VLM checkpoint through an image API."""

from contextlib import asynccontextmanager
from io import BytesIO
import json
import os
from pathlib import Path
from threading import Lock

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from PIL import Image, ImageOps, UnidentifiedImageError
import torch
from transformers import AutoProcessor, BitsAndBytesConfig, Qwen2_5_VLForConditionalGeneration

MAX_IMAGES = 4
MAX_IMAGE_BYTES = 2 * 1024 * 1024
MAX_BODY_BYTES = 9 * 1024 * 1024
DEVICE = "cuda:0"
INFERENCE_LOCK = Lock()
PROMPT = (
    "The pictures are ordered in time, earliest first. Is snow actively falling in the air "
    "during this sequence? Use visible evidence of particles or streaks across frames. Snow "
    "already on the ground, headlights, rain and blowing snow from the surface are insufficient "
    "evidence of falling snow. Use 'uncertain' if the distinction cannot be made. "
    'Return only JSON with keys "falling_snow" (yes, no, or uncertain) and '
    '"evidence" (one short sentence about what is actually visible).'
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    model_dir = Path(os.environ["WEATHER_VLM_MODEL"])
    if not torch.cuda.is_available():
        raise RuntimeError("PyTorch cannot access an NVIDIA GPU.")
    dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
    quantization = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_use_double_quant=True,
        bnb_4bit_compute_dtype=dtype,
    )
    app.state.processor = AutoProcessor.from_pretrained(
        model_dir, local_files_only=True, use_fast=False,
        min_pixels=4 * 28 * 28, max_pixels=256 * 28 * 28,
    )
    app.state.model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
        model_dir,
        local_files_only=True,
        quantization_config=quantization,
        torch_dtype=dtype,
        device_map={"": 0},
        attn_implementation="sdpa",
    ).eval()
    yield


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def check_request(request: Request, call_next):
    if request.method == "POST":
        length = request.headers.get("content-length", "")
        if not length.isascii() or not length.isdecimal():
            return JSONResponse({"detail": "Content-Length is required"}, status_code=411)
        if len(length) > 9 or int(length) > MAX_BODY_BYTES:
            return JSONResponse({"detail": "Request exceeds 9 MiB"}, status_code=413)
    return await call_next(request)


@app.get("/healthz")
def health():
    return {
        "status": "ready",
        "model": "deepguess/weather-vlm-qwen2.5-7b",
        "gpu": torch.cuda.get_device_name(0),
        "quantization": "nf4",
    }


def read_image(upload: UploadFile):
    data = upload.file.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(413, "Each image must be at most 2 MiB.")
    try:
        with Image.open(BytesIO(data), formats=["JPEG", "PNG", "WEBP"]) as image:
            if max(image.size) > 4096:
                raise HTTPException(413, "Image dimensions must not exceed 4096 pixels.")
            return ImageOps.exif_transpose(image).convert("RGB")
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise HTTPException(422, "Expected a valid JPEG, PNG, or WebP image.") from exc


def interpret_answer(text: str):
    text = text.strip()
    if text.startswith("```") and text.endswith("```"):
        text = text.partition("\n")[2].removesuffix("```").strip()
    try:
        result = json.loads(text)
        status = result["falling_snow"]
        evidence = result["evidence"]
        if status in ("yes", "no", "uncertain") and isinstance(evidence, str) and evidence.strip():
            return {"falling_snow": status, "evidence": evidence[:500], "format_valid": True}
    except (json.JSONDecodeError, KeyError, TypeError):
        pass
    return {"falling_snow": "uncertain", "evidence": "The model returned an invalid answer.", "format_valid": False}


def run_model(request, frames, instruction, max_new_tokens):
    processor = request.app.state.processor
    model = request.app.state.model
    messages = [{
        "role": "user",
        "content": [{"type": "image"} for _ in frames] + [{"type": "text", "text": instruction}],
    }]
    prompt = processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    inputs = processor(text=[prompt], images=frames, return_tensors="pt").to(DEVICE)
    with torch.inference_mode():
        generated = model.generate(**inputs, max_new_tokens=max_new_tokens, do_sample=False)
    return processor.decode(generated[0, inputs.input_ids.shape[1]:], skip_special_tokens=True)


@app.post("/v1/snow")
def analyze(
    request: Request,
    images: list[UploadFile] | None = File(None),
    image: UploadFile | None = File(None),
):
    if (image is None) == (images is None):
        raise HTTPException(422, "Send one image or a list of images.")
    uploads = [image] if image is not None else images
    if not 1 <= len(uploads) <= MAX_IMAGES:
        raise HTTPException(422, f"Send between 1 and {MAX_IMAGES} images.")
    if not INFERENCE_LOCK.acquire(blocking=False):
        raise HTTPException(429, "The GPU is busy.", headers={"Retry-After": "2"})
    try:
        frames = [read_image(upload) for upload in uploads]
        result = interpret_answer(run_model(request, frames, PROMPT, 96))
        if image is None:
            result["frames"] = len(frames)
        return result
    except torch.cuda.OutOfMemoryError as exc:
        raise HTTPException(503, "GPU memory exhausted. Retry with fewer frames.") from exc
    finally:
        INFERENCE_LOCK.release()


SCENE_VALUES = {
    "falling_snow": ("yes", "no", "uncertain"),
    "intensity": ("none", "light", "moderate", "heavy", "uncertain"),
    "visibility": ("good", "reduced", "poor", "uncertain"),
    "view_quality": ("good", "limited", "unusable"),
    "snow_on_ground": ("yes", "no", "uncertain"),
    "large_snowdrifts": ("yes", "no", "uncertain"),
    "road_coverage": ("clear", "partial", "mostly_covered", "not_visible", "uncertain"),
}
SCENE_PROMPT = (
    "Describe visible winter conditions from one image. Separate airborne snow from existing cover. "
    "Use heavy for dense streaks or flakes that obscure the view, light for clearly visible sparse flakes, "
    "and uncertain if glare, rain or windblown snow could explain the scene. "
    "Visibility is how well objects through the scene can be seen, not the image resolution. "
    "Road coverage applies only to visible vehicle lanes: a walkway, yard, parked cars and snow banks "
    "are not driving lanes. Use not_visible when no road surface can be seen. "
    "Do not infer depth, blocked lanes, or whether crews are working. "
    "Return only a JSON object with these keys and exactly one value per key: "
    + ", ".join(f"{key} ({'/'.join(values)})" for key, values in SCENE_VALUES.items())
    + '. Include "evidence", one short factual Russian sentence describing what is visible.'
)


def interpret_scene(text):
    value = text.strip()
    if value.startswith("```") and value.endswith("```"):
        value = value.partition("\n")[2].removesuffix("```").strip()
    response = {
        "schema_version": "1",
        "prompt_version": "snow-scene-1",
        "model": "deepguess/weather-vlm-qwen2.5-7b",
    }
    try:
        result = json.loads(value)
        if all(result[key] in allowed for key, allowed in SCENE_VALUES.items()):
            evidence = result["evidence"]
            if isinstance(evidence, str) and evidence.strip():
                return {**response, **{key: result[key] for key in SCENE_VALUES},
                        "evidence": evidence.strip()[:500], "format_valid": True}
    except (ValueError, KeyError, TypeError):
        pass
    return {**response, **{key: "uncertain" for key in SCENE_VALUES}, "view_quality": "unusable",
            "evidence": "Модель вернула ответ вне схемы.", "format_valid": False}


@app.post("/v1/scene")
def analyze_scene(request: Request, image: UploadFile = File(...)):
    if not INFERENCE_LOCK.acquire(blocking=False):
        raise HTTPException(429, "The GPU is busy.", headers={"Retry-After": "2"})
    try:
        return interpret_scene(run_model(request, [read_image(image)], SCENE_PROMPT, 256))
    except torch.cuda.OutOfMemoryError as exc:
        raise HTTPException(503, "GPU memory exhausted.") from exc
    finally:
        INFERENCE_LOCK.release()
