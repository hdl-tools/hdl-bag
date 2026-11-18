# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
#
# hdl-bag tools image. Versions come from tools.lock via --build-arg; the defaults below
# must match it, which tests/test_install_tools.py asserts.
#
# Every tool is installed by scripts/install_tools.py, the same installer a developer runs
# on macOS, so the image and a workstation get the pinned tools the same way from the same
# table. The build context is the repository root (see .dockerignore), because that is where
# the installer and tools.lock live.

ARG UBUNTU_VERSION=24.04

# ---------------------------------------------------------------- Verilator
FROM ubuntu:${UBUNTU_VERSION} AS verilator
ARG VERILATOR_VERSION=v5.052
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        autoconf bison build-essential ca-certificates flex git help2man libfl-dev \
        libfl2 perl python3 zlib1g-dev ccache \
    && rm -rf /var/lib/apt/lists/*
# Only the installer and this one ARG are inputs to this stage, so bumping any other pin
# still reuses the cached Verilator build instead of spending twenty minutes on it. That is
# why the version arrives through --set rather than by copying tools.lock in.
COPY scripts/tool_specs.py scripts/install_tools.py /install/
RUN python3 /install/install_tools.py --lock - --prefix /opt --platform linux-x64 \
        --set VERILATOR_VERSION=${VERILATOR_VERSION} verilator

# ---------------------------------------------------------------- final
FROM ubuntu:${UBUNTU_VERSION}
ARG PYTHON_VERSION=3.11
ARG UV_VERSION=0.9.26
ARG VERILATOR_VERSION=v5.052
ARG SLANG_VERSION=v12.0
ARG VERIBLE_VERSION=v0.0-4296-g0f262651
ARG OSS_CAD_SUITE_DATE=2026-10-02
ARG BENDER_VERSION=v0.32.1

RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        build-essential ca-certificates ccache curl git make perl python3 xz-utils zlib1g-dev \
        libreoffice-calc-nogui \
    && rm -rf /var/lib/apt/lists/*

COPY --from=verilator /opt/verilator /opt/verilator
COPY scripts/tool_specs.py scripts/install_tools.py /install/
COPY tools/tools.sha256 /install/tools.sha256

# One tool per RUN, as before, so bumping one pin re-downloads only that tool. Each archive
# is checked against /install/tools.sha256 before it is unpacked.
#
# The OSS CAD Suite brings its own copies of Verilator and slang; both are shadowed by the
# pinned ones through the PATH order below.
RUN python3 /install/install_tools.py --lock - --prefix /opt --platform linux-x64 \
        --digests /install/tools.sha256 \
        --set OSS_CAD_SUITE_DATE=${OSS_CAD_SUITE_DATE} oss-cad-suite
RUN python3 /install/install_tools.py --lock - --prefix /opt --platform linux-x64 \
        --digests /install/tools.sha256 --set SLANG_VERSION=${SLANG_VERSION} slang
RUN python3 /install/install_tools.py --lock - --prefix /opt --platform linux-x64 \
        --digests /install/tools.sha256 --set VERIBLE_VERSION=${VERIBLE_VERSION} verible
RUN python3 /install/install_tools.py --lock - --prefix /opt --platform linux-x64 \
        --digests /install/tools.sha256 --set BENDER_VERSION=${BENDER_VERSION} bender

# uv keeps its own installer: it drops both uv and uvx, and swapping it for the release
# archive risks shipping an image without uvx for no gain here.
RUN curl -fsSL "https://astral.sh/uv/${UV_VERSION}/install.sh" | env UV_INSTALL_DIR=/usr/local/bin sh \
    && uv python install ${PYTHON_VERSION}

# Kept literal because Docker cannot evaluate the installer.
# tests/test_install_tools.py asserts this equals path_line("/opt", "linux-x64").
ENV PATH="/opt/verilator/bin:/opt/slang/bin:/opt/verible/bin:/opt/bender/bin:${PATH}:/opt/oss-cad-suite/bin" \
    UV_PYTHON=${PYTHON_VERSION}

# Record what the image holds, and fail the build if any tool is not at its pin. LibreOffice
# comes from apt, so it is checked separately.
RUN python3 /install/install_tools.py --lock - --prefix /opt --platform linux-x64 --check \
        --set VERILATOR_VERSION=${VERILATOR_VERSION} --set SLANG_VERSION=${SLANG_VERSION} \
        --set VERIBLE_VERSION=${VERIBLE_VERSION} --set BENDER_VERSION=${BENDER_VERSION} \
        --set OSS_CAD_SUITE_DATE=${OSS_CAD_SUITE_DATE} \
    && soffice --version && uv --version \
    && command -v slang && command -v verilator

WORKDIR /work
