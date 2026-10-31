# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
#
# hdl-bag tools image. Versions come from tools.lock via --build-arg;
# the defaults below must match tools.lock.

ARG UBUNTU_VERSION=24.04

# ---------------------------------------------------------------- Verilator
FROM ubuntu:${UBUNTU_VERSION} AS verilator
ARG VERILATOR_VERSION=v5.052
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        autoconf bison build-essential ca-certificates flex git help2man libfl-dev \
        libfl2 perl python3 zlib1g-dev ccache \
    && rm -rf /var/lib/apt/lists/*
RUN git clone --depth 1 --branch ${VERILATOR_VERSION} https://github.com/verilator/verilator.git /src \
    && cd /src && autoconf && ./configure --prefix=/opt/verilator \
    && make -j"$(nproc)" && make install && rm -rf /src

# ---------------------------------------------------------------- final
FROM ubuntu:${UBUNTU_VERSION}
ARG PYTHON_VERSION=3.11
ARG UV_VERSION=0.9.26
ARG SLANG_VERSION=v12.0
ARG VERIBLE_VERSION=v0.0-4296-g0f262651
ARG OSS_CAD_SUITE_DATE=2026-10-01
ARG BENDER_VERSION=v0.32.1

RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        build-essential ca-certificates curl git make perl xz-utils zlib1g-dev \
        libreoffice-calc-nogui \
    && rm -rf /var/lib/apt/lists/*

COPY --from=verilator /opt/verilator /opt/verilator

# OSS CAD Suite: Yosys, SymbiYosys and solvers. Its bundled Verilator is shadowed by
# the pinned one above (PATH order).
RUN curl -fsSL "https://github.com/YosysHQ/oss-cad-suite-build/releases/download/${OSS_CAD_SUITE_DATE}/oss-cad-suite-linux-x64-$(echo ${OSS_CAD_SUITE_DATE} | tr -d -).tgz" \
    | tar -xz -C /opt

RUN mkdir -p /opt/slang \
    && curl -fsSL "https://github.com/MikePopoloski/slang/releases/download/${SLANG_VERSION}/slang-linux-x86_64.tar.gz" \
    | tar -xz -C /opt/slang --strip-components=1

RUN mkdir -p /opt/verible \
    && curl -fsSL "https://github.com/chipsalliance/verible/releases/download/${VERIBLE_VERSION}/verible-${VERIBLE_VERSION}-linux-static-x86_64.tar.gz" \
    | tar -xz -C /opt/verible --strip-components=1

RUN mkdir -p /opt/bender/bin \
    && curl -fsSL "https://github.com/pulp-platform/bender/releases/download/${BENDER_VERSION}/bender-x86_64-unknown-linux-gnu.tar.xz" \
    | tar -xJ -C /opt/bender/bin --strip-components=1

RUN curl -fsSL "https://astral.sh/uv/${UV_VERSION}/install.sh" | env UV_INSTALL_DIR=/usr/local/bin sh \
    && uv python install ${PYTHON_VERSION}

ENV PATH="/opt/verilator/bin:/opt/slang/bin:/opt/verible/bin:/opt/bender/bin:${PATH}:/opt/oss-cad-suite/bin" \
    UV_PYTHON=${PYTHON_VERSION}

# Print every tool version so the build log records what the image holds.
RUN verilator --version && slang --version && verible-verilog-lint --version \
    && bender --version && yosys -V && sby --help >/dev/null && soffice --version && uv --version

WORKDIR /work
