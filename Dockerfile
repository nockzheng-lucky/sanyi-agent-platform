FROM python:3.11-slim

WORKDIR /srv/sanyi-agent-platform

COPY pyproject.toml ./
COPY app ./app
RUN pip install --no-cache-dir .

ENV SANYI_DATA_DIR=/srv/sanyi-agent-platform/data
EXPOSE 8100

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8100"]
