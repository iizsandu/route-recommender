FROM python:3.11-slim

# WHY /repo/backend: mirrors the local repo layout (repo root = /repo, backend = /repo/backend).
# All Path(__file__).parents[N] calculations in backend code resolve to /repo as the
# "repo root", so relative env vars like KDE_ARTIFACTS_DIR=ml/artifacts resolve correctly
# to /repo/ml/artifacts without any special-casing.
WORKDIR /repo/backend

COPY backend/requirements.txt .

# WHY cpu wheel first: sentence-transformers depends on torch. Installing the CPU-only
# wheel here (~200 MB) prevents pip from pulling the default GPU wheel (~800 MB) when
# it resolves sentence-transformers' dependencies from requirements.txt.
RUN pip install --no-cache-dir torch==2.2.0 --index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir -r requirements.txt

# ffmpeg: faster-whisper uses it to decode WebM/Ogg/MP4 audio sent from the browser.
# WHY separate RUN from pip: keeps the apt cache layer independent so a pip change
# doesn't force re-downloading ffmpeg and vice versa.
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*


COPY backend/app/ ./app/

# ML module needed so pickle.load can reconstruct FixedBandwidthKDE objects.
# The class lives at ml.kde_model, which risk_model.py adds to sys.path at import.
COPY ml/kde_model.py /repo/ml/kde_model.py

# Pre-computed model artifacts baked into the image at build time.
# WHY bake-in: Container Apps has no persistent disk. Azure Blob mount adds
# operational complexity not justified for a portfolio project. Weekly retrain
# rebuilds and pushes a new image via the local build + deploy.ps1 workflow.
COPY ml/artifacts/ /repo/ml/artifacts/

# Retrieval module — Python code + BM25 model (dense embeddings are baked via the
# pre-download step below; no need to copy the sentence-transformers cache dir).
COPY retrieval/ /repo/retrieval/

# Pre-download the bge-small embedding model into the image layer so cold-starts
# don't hit HuggingFace at runtime (Container Apps has no persistent cache dir).
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-small-en-v1.5')"

# Pre-download Whisper base model weights into the image layer.
# WHY bake-in: Container Apps has no persistent cache; cold-start would hit
# HuggingFace on first request, adding 30-60s delay.
# WHY device=cpu here even if WHISPER_DEVICE=cuda at runtime: the model weights
# are device-agnostic — they download once and load onto whichever device is set
# in the env var at startup.
RUN python -c "from faster_whisper import WhisperModel; WhisperModel('base', device='cpu', compute_type='int8')"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
