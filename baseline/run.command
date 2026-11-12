#!/bin/zsh
cd -- "${0:A:h}"
if [[ -x .venv/bin/python ]]; then
  .venv/bin/python app.py
else
  python3 app.py
fi
echo "서버가 종료되었습니다. 창을 닫아도 됩니다."
read -k 1
