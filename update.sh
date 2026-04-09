#!/bin/bash
set -e

pdflatex -interaction=batchmode publications/publication_list.tex
biber --quiet publications/publication_list
pdflatex -interaction=batchmode publications/publication_list.tex
pdflatex -interaction=batchmode publications/publication_list.tex

echo "✓ PDF built"

MSG="${*:-update}"          # all args joined; default "update" if none given
git add -A
git commit -m "$MSG"
git push origin main

echo "✓ Pushed $MSG"