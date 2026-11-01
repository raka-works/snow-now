#!/bin/bash
# 4・6日前の過去予報の取得が終わったら、係数を出し直してページを作り、push する
cd /c/Users/ryow6/claude/snow-now
while tasklist //FI "IMAGENAME eq py.exe" | grep -q py.exe && ls data/verify2/*_?.json | xargs grep -L previous_day4_jma_gsm 2>/dev/null | grep -q .; do sleep 60; done
export PYTHONUTF8=1
py tools/verify2.py > tools/v3.log 2>&1 || exit 1
py build.py >> tools/v3.log 2>&1 || exit 1
git add data/params.json index.html r build.py tools/verify2.py
git commit -qm "4日前・6日前も検証して係数を出し直し（1〜7日前すべて）

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
git pull -q --no-rebase -X ours origin main; git push -q origin main
echo done >> tools/v3.log
