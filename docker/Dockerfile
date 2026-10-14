# Development tools only. No OMA runtime services, credentials, or host mounts.
FROM nvcr.io/nvidia/base/ubuntu@sha256:c280ee89f8bfcbdaba6179ad4347f60c509e841cbe63eb93002f01bf70e0819c
ARG CODEX_VERSION=0.162.1
# APT resolves security-updated packages at build time. Record the image digest
# and dpkg-query inventory after each approved build; see evidence/*-image.json.
# The base and Codex are pinned; the APT indexes and npm dependency graph are not
# an archived repository snapshot. Rebuilds are traceable, not bit-for-bit locked.
USER root
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      ca-certificates curl git nodejs npm python3 python3-venv python3-pytest \
    && npm install --global --ignore-scripts "@openai/codex@${CODEX_VERSION}" \
    && apt-get clean && rm -rf /var/lib/apt/lists/* /root/.npm
RUN mkdir -p /sandbox && chown 1000:1000 /sandbox
USER 1000:1000
WORKDIR /sandbox
CMD ["/bin/sleep", "infinity"]
