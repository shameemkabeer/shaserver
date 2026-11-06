# ---- docker CLI + compose plugin (to drive the host daemon via its socket) ----
FROM docker:28-cli AS dockercli

# ---- Inter font, served to browsers so the UI looks the same everywhere ----
FROM python:3.13-slim AS fonts
RUN apt-get update && apt-get install -y --no-install-recommends fonts-inter \
    && rm -rf /var/lib/apt/lists/*

# ---- runtime ----
FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    DOCKER_CONFIG=/tmp/.docker HOME=/tmp
COPY --from=dockercli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=dockercli /usr/local/libexec/docker/cli-plugins/docker-compose /usr/local/libexec/docker/cli-plugins/docker-compose
WORKDIR /app
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/main.py .
COPY templates ./templates
COPY frontend ./static
COPY --from=fonts /usr/share/fonts/opentype/inter/Inter-Regular.otf \
                  /usr/share/fonts/opentype/inter/Inter-Medium.otf \
                  /usr/share/fonts/opentype/inter/Inter-SemiBold.otf \
                  /usr/share/fonts/opentype/inter/Inter-Bold.otf \
                  /usr/share/fonts/opentype/inter/InterDisplay-Bold.otf \
                  /usr/share/fonts/opentype/inter/InterDisplay-Light.otf \
                  /usr/share/fonts/opentype/inter/InterDisplay-ExtraLight.otf \
                  /usr/share/fonts/opentype/inter/InterDisplay-SemiBold.otf \
                  ./static/fonts/
COPY --from=fonts /usr/share/doc/fonts-inter/copyright ./static/fonts/LICENSE.txt
EXPOSE 8088
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:8088/api/health', timeout=4)" || exit 1
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8088", "--ws", "wsproto", "--no-server-header", "--log-level", "warning"]
