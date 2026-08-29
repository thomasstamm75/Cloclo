# Image minimale : aucune dépendance hors bibliothèque standard.
FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY cloclo ./cloclo
RUN pip install --no-cache-dir . && useradd --create-home cloclo
USER cloclo

EXPOSE 8787
ENV PYTHONUNBUFFERED=1
HEALTHCHECK --interval=30s --timeout=5s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8787/healthz', timeout=3).status == 200 else 1)"

ENTRYPOINT ["cloclo"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8787"]
