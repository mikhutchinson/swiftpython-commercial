#!/usr/bin/env bash
set -euo pipefail
demo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
"$demo_dir/build-app.sh"
if [ "$#" = 0 ]; then
    open "$demo_dir/build/Particle Showcase.app"
else
    exec "$demo_dir/build/Particle Showcase.app/Contents/MacOS/particle-showcase" "$@"
fi
