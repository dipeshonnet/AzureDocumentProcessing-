#!/usr/bin/env sh
set -eu

cd "$(dirname "$0")/.."

python -m pip install -r requirements.txt
python -B -m pytest backend/tests evals -p no:cacheprovider
python -m evals.run_evals

cd frontend
npm ci
npm test
npm run build
