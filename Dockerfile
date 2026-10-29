# Pin the inspected official Python image for the linux/amd64 build.
FROM docker.io/library/python@sha256:623674a14ef93d052bb9b0de4dbf5f28eb578a35263838941cc8aa43213c1487

# Avoid runtime bytecode writes on the read-only filesystem.
ENV PYTHONDONTWRITEBYTECODE=1
# Deliver Python output without application-level buffering.
ENV PYTHONUNBUFFERED=1

# Use a fixed directory for runtime source and dependency installation.
WORKDIR /app

# Copy the existing runtime requirements before application source.
COPY requirements.txt /app/requirements.txt

# Install binary runtime dependencies from the existing approved index.
# Ignore inherited pip configuration, avoid download caches, and verify compatibility.
RUN python -m pip --isolated install --no-cache-dir \
    --index-url https://pypi.org/simple --only-binary=:all: \
    -r /app/requirements.txt \
    && python -m pip check

# Create a dedicated identity without a login shell or home directory.
RUN groupadd --gid 10001 gateway \
    && useradd --uid 10001 --gid 10001 --no-create-home \
    --home-dir /nonexistent --shell /usr/sbin/nologin gateway

# Copy only the allowlisted Python modules; leave ownership with root.
COPY src/gateway/ /app/src/gateway/

# Run the service without root privileges.
USER 10001:10001

# Document the internal port; host publication is a separate runtime choice.
EXPOSE 8000

# Start only the existing audited, non-executing API.
# The internal bind permits container networking; publish to host loopback only.
CMD ["python", "-m", "uvicorn", "gateway.api_startup:create_postgresql_app", "--factory", "--app-dir", "/app/src", "--host", "0.0.0.0", "--port", "8000"]
