#!/bin/bash
cd $(dirname $0)
git fetch
git reset --hard origin/`git rev-parse --abbrev-ref HEAD`
# if pipenv in not in the path, add ~/.local/bin/ to the path
if ! command -v pipenv &> /dev/null
then
    export PATH="$HOME/.local/bin/:$PATH"
fi
pipenv install
pipenv run python run.py
