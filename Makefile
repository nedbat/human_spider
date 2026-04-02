.PHONY: spider quality

spider:
	-rm -f data/*
	@-grep Found out.txt
	uv run spider.py > out.txt
	grep Found out.txt

quality:
	uvx ruff check --fix *.py
	uvx mypy --ignore-missing-imports *.py
