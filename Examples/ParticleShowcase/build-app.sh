#!/usr/bin/env bash
set -euo pipefail
demo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$demo_dir/../../scripts/build_demo_app.sh" ParticleShowcase "$@"
