#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

if [[ -x "${ROOT_DIR}/.venv/bin/python" ]]; then
  PYTHON="${ROOT_DIR}/.venv/bin/python"
elif [[ -x "${ROOT_DIR}/venv/bin/python" ]]; then
  PYTHON="${ROOT_DIR}/venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON="$(command -v python3)"
else
  echo "Erro: python3 nao encontrado." >&2
  exit 1
fi

for MODULE in shapefile pyproj shapely sqlalchemy; do
  if ! "${PYTHON}" -c "import ${MODULE}" >/dev/null 2>&1; then
    echo "Erro: modulo '${MODULE}' ausente. Instale as dependencias do pyproject.toml antes de executar." >&2
    exit 1
  fi
done

cd "${ROOT_DIR}"
exec "${PYTHON}" scripts/importar_setor_shapefile.py "$@"
