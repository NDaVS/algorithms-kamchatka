#!/usr/bin/env bash
set -euo pipefail

diagnose() {
    cat /etc/os-release
    uname -m
    printf '\n=== GPU and driver ===\n'
    if command -v nvidia-smi >/dev/null; then
        nvidia-smi
    else
        printf 'nvidia-smi is missing\n'
        if command -v lspci >/dev/null; then lspci | grep -Ei 'VGA|3D|NVIDIA' || true; fi
    fi
    printf '\n=== Python, RAM and disk ===\n'
    python3 --version || true
    free -h
    df -h "$HOME"
    printf '\n=== Relevant listening ports ===\n'
    ss -ltn '( sport = :80 or sport = :443 or sport = :8000 )' || true
    printf '\n=== Hugging Face DNS ===\n'
    getent hosts huggingface.co || true
}

run_mounted() {
    local storage="${WEATHER_VLM_STORAGE:-$HOME/kamchatka-storage}"
    local source_dir
    source_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
    export WEATHER_VLM_MODEL="${WEATHER_VLM_MODEL:-$storage/weather-vlm-qwen2.5-7b}"

    if [[ ! -d "$storage/python-packages" || ! -s "$WEATHER_VLM_MODEL/model.safetensors.index.json" ]]; then
        printf 'Check the mounted model and python-packages under %s.\n' "$storage" >&2
        return 1
    fi

    python3 - "$WEATHER_VLM_MODEL" <<'PY'
import json
from pathlib import Path
import sys

model = Path(sys.argv[1])
index = json.loads((model / "model.safetensors.index.json").read_text())
files = set(index["weight_map"].values()) | {"config.json", "preprocessor_config.json", "tokenizer.json"}
missing = sorted(name for name in files if not (model / name).is_file() or not (model / name).stat().st_size)
if missing:
    sys.exit("Missing or empty checkpoint files: " + ", ".join(missing))
PY

    mkdir -p -- "$storage/tmp"
    export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
    export PYTHONPATH="$storage/python-packages${PYTHONPATH:+:$PYTHONPATH}" TMPDIR="$storage/tmp"
    exec python3 -m uvicorn weather_vlm_api:app --app-dir "$source_dir" \
        --host 127.0.0.1 --port "${WEATHER_VLM_PORT:-11436}" --workers 1 --limit-concurrency 8
}

case "${1:---diagnose}" in
    --diagnose)
        diagnose
        exit 0
        ;;
    --run-mounted)
        run_mounted
        exit 0
        ;;
    --install) ;;
    *)
        printf 'Usage: %s [--diagnose|--run-mounted|--install]\n' "$0" >&2
        exit 2
        ;;
esac

if [[ $(id -u) -eq 0 ]]; then
    printf 'Run as a regular user with sudo.\n' >&2
    exit 1
fi
if [[ $(uname -m) != x86_64 ]]; then
    printf 'This installer uses Linux x86_64 CUDA wheels.\n' >&2
    exit 1
fi
if ! nvidia-smi -L; then
    printf 'Install or repair the NVIDIA driver first. See docs/WEATHER_VLM_UBUNTU.md.\n' >&2
    exit 1
fi

source_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ ! -f "$source_dir/weather_vlm_api.py" ]]; then
    printf 'Place weather_vlm_api.py next to the installer.\n' >&2
    exit 1
fi
install_root="$HOME/weather-vlm"
unit_dir="$HOME/.config/systemd/user"
python="$install_root/.venv/bin/python"

sudo apt-get update
sudo apt-get install -y python3 python3-venv ca-certificates curl
python3 - <<'PY'
import sys
if not (3, 10) <= sys.version_info[:2] <= (3, 13):
    raise SystemExit("This install uses Python 3.10–3.13. Ubuntu 22.04 and 24.04 provide supported versions.")
PY
systemctl --user show-environment >/dev/null

mkdir -p "$install_root" "$unit_dir"
install -m 0644 "$source_dir/weather_vlm_api.py" "$install_root/weather_vlm_api.py"
python3 -m venv "$install_root/.venv"
export PIP_NO_CACHE_DIR=1
"$python" -m pip install --upgrade pip
"$python" -m pip install 'torch==2.8.0' 'torchvision==0.23.0' --index-url https://download.pytorch.org/whl/cu128
"$python" -m pip install 'transformers>=4.57,<5' 'bitsandbytes>=0.46,<1' 'accelerate>=1.6,<2' 'huggingface_hub>=0.34,<1' pillow fastapi uvicorn python-multipart
"$python" -m pip check

"$python" - <<'PY'
import torch
from transformers import Qwen2_5_VLForConditionalGeneration
import bitsandbytes

if not torch.cuda.is_available():
    raise SystemExit("CUDA is unavailable to PyTorch. Check nvidia-smi and the installed torch wheel.")
gpu = torch.cuda.get_device_properties(0)
print(f"torch={torch.__version__} CUDA={torch.version.cuda} bitsandbytes={bitsandbytes.__version__}")
print(f"GPU {gpu.name}: {gpu.total_memory / 1024**3:.1f} GiB, BF16={torch.cuda.is_bf16_supported()}")
dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
layer = bitsandbytes.nn.Linear4bit(
    64, 64, bias=False, compute_dtype=dtype, quant_type="nf4",
).to("cuda")
with torch.inference_mode():
    result = layer(torch.zeros(1, 64, device="cuda", dtype=dtype))
torch.cuda.synchronize()
assert result.shape == (1, 64)
print("CUDA NF4 test passed")
PY

"$python" - "$install_root/model" <<'PY'
from pathlib import Path
import sys
from huggingface_hub import snapshot_download

repo = "deepguess/weather-vlm-qwen2.5-7b"
snapshot_download(
    repo_id=repo,
    local_dir=Path(sys.argv[1]),
    allow_patterns=["*.safetensors", "*.json", "merges.txt"],
    ignore_patterns=["training_*"],
)
PY

cat > "$unit_dir/weather-vlm.service" <<'UNIT'
[Unit]
Description=Kamchatka Weather VLM API

[Service]
Environment=WEATHER_VLM_MODEL=%h/weather-vlm/model
Environment=HF_HUB_OFFLINE=1
Environment=TRANSFORMERS_OFFLINE=1
WorkingDirectory=%h/weather-vlm
ExecStart=%h/weather-vlm/.venv/bin/python -m uvicorn weather_vlm_api:app --host 127.0.0.1 --port 8000 --workers 1 --limit-concurrency 8
Restart=on-failure
RestartSec=10
TimeoutStopSec=30
UMask=0077
NoNewPrivileges=yes

[Install]
WantedBy=default.target
UNIT

sudo loginctl enable-linger "$(id -un)"
systemctl --user daemon-reload
systemctl --user enable weather-vlm.service
systemctl --user restart weather-vlm.service
printf '\nInspect startup: journalctl --user -u weather-vlm -f\n'
