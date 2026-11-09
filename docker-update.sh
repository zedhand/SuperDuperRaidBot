#!/bin/sh
# Update the bot to the newest published release and restart it (Docker / Raspberry Pi). See DOCKER.md.
# Your settings (.env) and data (the data folder: raidbot.db, backups, raidbot.log) are never touched,
# and the database upgrades itself when the new version starts.
#
#   ./docker-update.sh            download the ready-made image for the newest release
#   ./docker-update.sh --build    build the image from the source on this machine instead
#
# Everything runs inside main(), so the shell has read the whole script before git replaces this file.
set -eu

main() {
    cd "$(dirname "$0")"
    build=no
    if [ "${1:-}" = "--build" ]; then build=yes; fi

    tag=$(curl -fsSL https://api.github.com/repos/zedhand/SuperDuperRaidBot/releases/latest \
          | sed -n 's/.*"tag_name": *"\([^"]*\)".*/\1/p' | head -n 1) || tag=""
    if [ -z "$tag" ]; then
        echo "Couldn't ask GitHub for the latest release. Check the internet connection and try again."
        exit 1
    fi
    echo "Latest release: $tag"
    git fetch --quiet --tags origin
    if git cat-file -e "$tag:docker-compose.yml" 2>/dev/null; then
        git -c advice.detachedHead=false checkout --quiet "$tag"
    else
        echo "($tag doesn't include the Docker files yet, so staying on the version you have: $(git describe --tags --always))"
    fi
    mkdir -p data

    if [ "$build" = no ] && ! grep -q '^ *build:' docker-compose.yml; then
        if docker compose pull; then
            docker compose up -d
        else
            echo "Couldn't download the ready-made image, so building it here instead (a few minutes on a Pi)."
            build=yes
        fi
    elif [ "$build" = no ]; then
        build=old   # an older release whose docker-compose.yml still builds the image itself
    fi
    if [ "$build" = yes ]; then
        docker compose -f docker-compose.yml -f docker-compose.build.yml up -d --build
    elif [ "$build" = old ]; then
        docker compose up -d --build
    fi

    docker image prune -f > /dev/null   # remove the previous version's leftover image layers
    echo "Done. Watch it start with:  docker compose logs -f   (Ctrl+C stops watching; the bot keeps running)"
}

main "$@"
