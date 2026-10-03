FROM python:3.12-slim-bookworm AS builder

WORKDIR /build
COPY pyproject.toml README.md LICENSE /build/
COPY src/ /build/src/
COPY scripts/verify_wheel.py /build/scripts/verify_wheel.py
RUN python -m pip wheel --no-deps --wheel-dir /wheels . \
    && python scripts/verify_wheel.py /wheels

FROM python:3.12-slim-bookworm

LABEL org.opencontainers.image.source="https://github.com/qorud02/public-data-sentinel" \
      org.opencontainers.image.description="Check CSV, TSV and JSON extracts against explicit data contracts." \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY --from=builder /wheels /wheels
COPY LICENSE /app/LICENSE
RUN python -m pip install --no-index --no-deps /wheels/*.whl \
    && python -I -c "from importlib.metadata import version; print(version('public-data-sentinel'))" \
    && rm -rf /wheels \
    && mkdir /work

USER 10001:10001
WORKDIR /work
ENTRYPOINT ["python", "-P", "-m", "public_data_sentinel.cli"]
CMD ["--help"]
