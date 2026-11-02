FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /srv/toe_kotak

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir --upgrade pip setuptools wheel

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY app /srv/toe_kotak/app
COPY run.py /srv/toe_kotak/run.py
COPY pytest.ini /srv/toe_kotak/pytest.ini
COPY tests /srv/toe_kotak/tests

RUN useradd --create-home --uid 10002 toekotak \
    && chown -R toekotak:toekotak /srv/toe_kotak
USER toekotak

EXPOSE 8005

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8005/toe/ready || exit 1

CMD ["python", "run.py"]
