docker run --rm \
  -v "$PWD:/work" \
  -v hdl-bag-uv:/root/.cache/uv \
  -w /work \
  ghcr.io/hdl-tools/hdl-bag-tools:v1.0.0 \
  sh -c "uv tool run --from 'hdl-bag[sim]==1.0.0' bag lint out/"
