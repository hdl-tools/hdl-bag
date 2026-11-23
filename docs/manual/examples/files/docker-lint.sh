docker run --rm \
  -v "$PWD:/work" \
  -v hdl-bag-venv:/venv \
  -e UV_PROJECT_ENVIRONMENT=/venv \
  -e UV_LINK_MODE=copy \
  -w /work \
  ghcr.io/hdl-tools/hdl-bag-tools:main \
  sh -c 'uv run bag lint out/'
