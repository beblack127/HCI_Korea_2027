#!/bin/zsh
cd -- "${0:A:h}"
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
echo "설치가 끝났습니다. run.command로 실행하세요."
read -k 1
