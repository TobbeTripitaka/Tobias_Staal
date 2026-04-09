#!/bin/bash
set -e

pdflatex -interaction=batchmode publications/publication_list.tex
biber --quiet publications/publication_list
pdflatex -interaction=batchmode publications/publication_list.tex
pdflatex -interaction=batchmode publications/publication_list.tex

echo "✓ PDF built"

git add .
git commit -m "Add new papers"
git push origin main

echo "✓ Pushed"