# Weather VLM на Ubuntu

Разворачивается исходный чекпойнт [deepguess/weather-vlm-qwen2.5-7b](https://huggingface.co/deepguess/weather-vlm-qwen2.5-7b). Четыре BF16-файла весов занимают 16 584 414 544 байта. При запуске Transformers загружает модель в NF4 через bitsandbytes. На RTX 4060 Ti с 8 ГБ фактическое потребление и скорость нужно измерить. Для установки ориентируйся на 30 ГБ свободного места в домашней папке.

## Запуск с имеющимися весами

Команда `bash cameras/deploy_weather_vlm.sh --run-mounted` читает чекпойнт и пакеты с примонтированного диска и запускает локальный API на `127.0.0.1:11436`. Путь настраивается через `WEATHER_VLM_STORAGE`.

## 1. Диагностика

Команды выполняются под обычным пользователем на домашнем Ubuntu. Для нативной Ubuntu 22.04 или 24.04 подходит системный Python 3.10 или 3.12. Для других версий установщик проверит Python 3.10–3.13 и x86_64.

```bash
cat /etc/os-release
uname -m
nvidia-smi
python3 --version
free -h
df -h "$HOME"
ss -ltn '( sport = :80 or sport = :443 or sport = :8000 )'
getent hosts huggingface.co
curl -fsSI --max-time 15 https://huggingface.co/deepguess/weather-vlm-qwen2.5-7b
```

Если `nvidia-smi` видит RTX 4060 Ti и выводит версию драйвера, переходи к установке. Если команда отсутствует или не видит GPU на нативной Ubuntu, проверь обнаружение видеокарты и рекомендованный драйвер.

```bash
sudo apt-get update
sudo apt-get install -y pciutils ubuntu-drivers-common
lspci | grep -Ei 'VGA|3D|NVIDIA'
ubuntu-drivers devices
sudo ubuntu-drivers install
sudo reboot
```

Перезагрузка завершит текущую сессию. После повторного входа снова выполни `nvidia-smi`. Для WSL ставится драйвер на Windows-хосте, а видеокарта должна быть видна внутри Ubuntu через `nvidia-smi`. [Установка драйверов Ubuntu](https://documentation.ubuntu.com/server/how-to/graphics/install-nvidia-drivers/) · [CUDA в WSL](https://docs.nvidia.com/cuda/wsl-user-guide/index.html)

## 2. Установка и запуск

Скопируй в одну папку на домашнем ПК [deploy_weather_vlm.sh](../cameras/deploy_weather_vlm.sh) и [weather_vlm_api.py](../cameras/weather_vlm_api.py). Если репозиторий уже есть на Ubuntu, команды выполняются из его корня.

```bash
bash cameras/deploy_weather_vlm.sh --diagnose
bash cameras/deploy_weather_vlm.sh --install
```

Если скопированы только два файла и ты находишься в их папке:

```bash
bash deploy_weather_vlm.sh --diagnose
bash deploy_weather_vlm.sh --install
```

Установщик ставит системные зависимости, создаёт `~/weather-vlm/.venv`, устанавливает PyTorch 2.8 с CUDA 12.8, Transformers и bitsandbytes, запускает тест операции NF4, загружает модель в `~/weather-vlm/model` и включает пользовательский сервис. Модель загружается с домашнего интернета. При обрыве запускай установщик повторно с тем же пользователем. Через `loginctl enable-linger` сервис запускается и без открытой RDP-сессии. [Пакеты PyTorch](https://pytorch.org/get-started/previous-versions/) · [Квантизация bitsandbytes](https://huggingface.co/docs/transformers/quantization/bitsandbytes) · [Загрузка Hugging Face](https://huggingface.co/docs/huggingface_hub/guides/download)

```bash
systemctl --user status weather-vlm --no-pager
journalctl --user -u weather-vlm -n 80 --no-pager
nvidia-smi
```

Первый запуск может занять время, пока модель загружается в память. Готовность проверяется запросом.

## 3. Проверка API на домашнем ПК

Выполни в Bash. Путь `$HOME/frame.jpg` замени на путь к реальному JPEG. Чтобы проверить серию, добавляй по одному `-F 'images=@...'` на каждый кадр в порядке от раннего к позднему. API принимает до четырёх JPEG, PNG или WebP, до 2 МиБ каждый. После предобработки размер визуального входа ограничен, чтобы снизить потребление VRAM.

```bash
BASE=http://127.0.0.1:8000

curl -fsS "$BASE/healthz"
curl -fsS --max-time 180 \
  -F "images=@$HOME/frame.jpg" "$BASE/v1/snow"
```

`/healthz` возвращает имя модели, GPU и режим NF4 после загрузки весов. `/v1/snow` возвращает `falling_snow` со значением `yes`, `no` или `uncertain`, текст признака, число кадров и `format_valid`. Ответ `uncertain` допустим, в том числе при одном кадре. `format_valid=false` означает, что модель не вернула ожидаемый формат.

## 4. Домен и HTTPS

API слушает `127.0.0.1:8000`. Для домена направь DNS A-запись на внешний IP домашнего подключения и пробрось TCP 80 и 443 на Ubuntu. Добавляй AAAA только при рабочем IPv6. Если 80 или 443 уже заняты, добавь домен в конфигурацию существующего HTTPS-прокси с upstream `http://127.0.0.1:8000`. На свободных портах можно поставить Caddy.

```bash
sudo apt-get install -y caddy
DOMAIN=weather.example.com  # замени на свой домен
sudo mkdir -p /etc/caddy/sites-enabled
sudo tee /etc/caddy/sites-enabled/weather-vlm.caddy >/dev/null <<EOF_CADDY
$DOMAIN {
    reverse_proxy 127.0.0.1:8000
}
EOF_CADDY
if ! sudo grep -qxF 'import /etc/caddy/sites-enabled/*.caddy' /etc/caddy/Caddyfile; then
  printf '\nimport /etc/caddy/sites-enabled/*.caddy\n' | sudo tee -a /etc/caddy/Caddyfile >/dev/null
fi
sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
sudo systemctl enable --now caddy
sudo systemctl reload caddy
sudo ufw status
```

Если UFW уже активен, открой порты.

```bash
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
```

Caddy получит сертификат, когда DNS и проброс начнут вести на него. Проверь с устройства за пределами домашней сети. [Автоматический HTTPS Caddy](https://caddyserver.com/docs/automatic-https)

```bash
DOMAIN=weather.example.com
curl -fsS "https://$DOMAIN/healthz"
```

Для проверки TLS и DNS смотри `sudo journalctl -u caddy -n 80 --no-pager`. Для проверки VRAM смотри `nvidia-smi` во время запроса. При `503` сначала проверь один кадр и наличие других процессов на GPU.

## Состояние проверки

Подтверждены тип модели, конфигурация процессора и размеры файлов Hugging Face. Синтаксис Bash и Python проверен на рабочем компьютере. Текущее состояние рабочего сервера и внешний адрес описаны в начале документа.

## Подключение к зимнему мониторингу

В конфигурации локального предпросмотра указан общий API `http://llm.eazyancloud.ru:48763`.

Экспериментальный `POST /v1/snow` был проверен с четырьмя контрольными снимками. Два кадра вернули `no`, сильный снегопад и снимок тротуара вернули `yes`.

Совместимый внешний экземпляр Weather VLM предоставляет `POST /v1/snow`, `POST /v1/scene` и `POST /v2/scene`. Мониторинг использует `MODEL_CONTRACT=scene_v2` и поле `image`. V2 возвращает интенсивность, признаки на видимой дороге, снег на земле, валы и оценки обзора конкретных частей кадра. При неполном ответе признак падающего снега проверяется через `/v1/snow`. Заключение в мониторинге формируется по-русски. Для рабочего контура задайте защищённый адрес `MODEL_URL` и при необходимости `MODEL_TOKEN`. Локальный код GPU-сервиса из этого каталога поддерживает собственную схему `/v1/scene`.
