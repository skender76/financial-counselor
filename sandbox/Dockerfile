# Immagine del sandbox di sviluppo (vedi README.md, «Sandbox di sviluppo»).
# Contiene solo Python e le dipendenze: il codice del progetto NON entra
# nell'immagine, arriva dal volume montato in /work.
FROM python:3.14-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends git \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/requirements.txt
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --no-cache-dir -r /tmp/requirements.txt \
 && rm /tmp/requirements.txt

# CLI dell'agente: comando di shell passato alla costruzione (vedi sandbox.sh,
# SANDBOX_AGENT_INSTALL). Vuoto = nessuna. Nessun fornitore è cablato qui.
ARG AGENT_INSTALL=""
RUN if [ -n "$AGENT_INSTALL" ]; then sh -c "$AGENT_INSTALL"; fi

# Segno di riconoscimento "sono nel sandbox" per sandbox/require-sandbox.sh.
RUN echo dashboard-sandbox-v1 > /etc/dashboard-sandbox

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/tmp/home

WORKDIR /work/repo
CMD ["bash"]
