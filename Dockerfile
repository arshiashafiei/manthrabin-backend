FROM docker.mobinhost.com/library/python:3.12 AS builder
LABEL maintainer="SMSPA Team <arshia2562@gmail.com>"

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl libpq-dev \
    python3-dev \
    libmariadb-dev-compat \
    pkg-config \
    && rm -rf /var/lib/apt/lists/* /usr/share/doc /usr/share/man \
    && apt-get clean \
    && useradd -m python
    
USER python
    
ENV PATH="/home/python/.local/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY --chown=python:python requirements.txt ./

RUN pip3 install --user --upgrade pip && \
    pip3 install --user -r requirements.txt

###############################################################################

FROM docker.mobinhost.com/library/python:3.12 AS run-time
LABEL maintainer="SMSPA Team <arshia2562@gmail.com>"

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libmariadb-dev-compat libcurl4-gnutls-dev librtmp-dev libpq-dev \
    && rm -rf /var/lib/apt/lists/* /usr/share/doc /usr/share/man \
    && apt-get clean \
    && useradd --create-home --no-log-init python \
    && mkdir -p /app/static /app/media \
    && chown python:python -R /app

USER python

COPY --from=builder --chown=python:python /home/python/.local /home/python/.local

ARG DEBUG="false"
ENV DEBUG="${DEBUG}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH="." \
    PATH="${PATH}:/home/python/.local/bin" \
    USER="python"

COPY --chown=python:python . .

RUN chmod +x /app/entrypoint.sh && \
    if [ "${DEBUG}" = "false" ]; then \
        DJANGO_SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(64))')" \
        DJANGO_ALLOWED_HOSTS=localhost python manage.py collectstatic --no-input; fi

EXPOSE 8000

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["--host", "127.0.0.1", "--port", "8000", "--workers", "4", "--log-level", "info"]
# CMD ["--host", "127.0.0.1", "--port", "8000", "--reload"]
