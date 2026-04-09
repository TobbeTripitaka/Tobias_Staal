#!/bin/bash

cd publications

pdflatex -interaction=batchmode publication_list.tex
biber --quiet publication_list
pdflatex -interaction=batchmode publication_list.tex
pdflatex -interaction=batchmode publication_list.tex

echo "✓ PDF built"

cd ..

mv publications/publication_list.pdf .

echo "✓ PDF moved to $(pwd)"

git add .
git diff --cached --quiet && echo "Nothing to commit" || (git commit -m "Add new papers" && git push origin main && echo "✓ Pushed")