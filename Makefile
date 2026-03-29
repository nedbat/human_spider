spider:
	uv run spider.py > out.txt; grep Found out.txt

quality:
	uvx ruff check --fix *.py
	uvx ty check *.py
