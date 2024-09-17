#!/bin/bash
cd $(dirname $0)
git fetch
git reset --hard origin/`git rev-parse --abbrev-ref HEAD`
pipenv install
pipenv run python run.py
