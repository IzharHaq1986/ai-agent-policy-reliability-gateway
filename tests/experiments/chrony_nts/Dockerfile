FROM docker.io/library/ubuntu@sha256:78d4744ead1f5b19b04d0df558ab182a5300903900755e515b2ca4d10eb2044d

# Prevent package scripts from starting a time service during image preparation.
RUN printf '#!/bin/sh\nexit 101\n' > /usr/sbin/policy-rc.d \
    && chmod 755 /usr/sbin/policy-rc.d \
    && apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        chrony=4.5-1ubuntu4.2 ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 ntslab \
    && useradd --uid 10001 --gid 10001 --no-create-home \
        --shell /usr/sbin/nologin ntslab \
    && mkdir /experiment \
    && dpkg-query -W -f='${binary:Package}\t${Version}\n' \
        > /experiment/package-versions.txt

COPY chrony.conf /experiment/chrony.conf
WORKDIR /experiment
USER 10001:10001

# The runtime also requires all capabilities dropped, including clock control.
ENTRYPOINT ["/usr/sbin/chronyd", "-U", "-x", "-d", "-u", "ntslab", "-f", "/experiment/chrony.conf"]
