# ReCADBench Open app Space (sdk: docker, public, free cpu-basic): the Gradio site AND the scorer.
# space/deploy_hf.py stages this file as the Space's Dockerfile; the Space repo is the build
# context: Dockerfile, README.md, requirements.txt (pinned by stage), spec/, evaluator/ (no tests),
# the app's *.py, tasks_public.json and assets/. Uploads are scored in-Space (RCB_MODE=space): a
# subprocess `python -I -m recadbench_eval.cli score-submission` started without HF_TOKEN / OAUTH_*.
# The private GT dataset is downloaded at startup to $HOME/.rcb-gt, outside the app folder.
# The OCP base (FROM, ENV, apt runtime libs) is the one of space/Dockerfile.eval: deploy_hf.py
# refuses to stage when the two drift apart. No GT, no tokens, no submitted code in the image.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    MPLBACKEND=Agg

# OCP (OpenCascade wheels) needs a few X/GL runtime libraries even when rendering off-screen.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgl1 libglu1-mesa libxrender1 libxext6 libsm6 \
    libfontconfig1 libegl1 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/recadbench
COPY spec ./spec
COPY evaluator ./evaluator
COPY requirements.txt ./requirements.txt

# One resolve: evaluator (build123d==0.13.0 -> cadquery-ocp 8.x) + the pinned Space requirements.
RUN pip install --upgrade pip \
 && pip install ./spec ./evaluator -r requirements.txt \
 && python -c "import recadbench_eval, build123d, OCP, gradio, huggingface_hub, filelock, fastapi, uvicorn; print('space image ok', recadbench_eval.__version__, gradio.__version__)"

# The site and the scorer both run as uid 1000 (Hugging Face runs the container as uid 1000).
# The scorer parses untrusted STEP files, so the app code and /assets stay root-owned and
# read-only for that user (no --chown): a scorer bug cannot plant files the site serves or
# imports. The app writes only to $HOME/.rcb-*, ~/.cache and the Gradio temp folder.
RUN useradd -m -u 1000 user
WORKDIR /opt/rcb/app
COPY *.py *.json ./
COPY assets ./assets
USER user
# SYSTEM=spaces: Gradio uses the real Hugging Face OAuth routes only when it is set (a Docker
# Space does not set it); without it every visitor would be signed in as the HF_TOKEN owner.
# PYTHONNOUSERSITE: nothing is imported from the writable ~/.local.
ENV HOME=/home/user \
    SYSTEM=spaces \
    PYTHONNOUSERSITE=1 \
    GRADIO_SERVER_NAME=0.0.0.0 \
    GRADIO_SERVER_PORT=7860 \
    GRADIO_ANALYTICS_ENABLED=False \
    HF_HUB_DISABLE_TELEMETRY=1
EXPOSE 7860
# No ENTRYPOINT and no init wrapper (tini, dumb-init, sh -c): the exec-form CMD makes the app
# PID 1, and the app makes itself non-dumpable before it starts a scorer. Any other PID 1 would
# keep HF_TOKEN and OAUTH_CLIENT_SECRET in a /proc/1/environ the scorer (same uid) can read.
# As PID 1 the app reaps the scorer processes it inherits (jobs.LocalDispatcher.reap).
CMD ["python", "app.py"]
