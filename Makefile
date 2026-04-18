.PHONY: spider quality

spider:
	@-grep Found out.txt > last_found.txt
	-rm -f data/*
	uv run spider.py > out.txt
	@echo === last time ===
	@cat last_found.txt
	@rm last_found.txt
	@echo === this time ===
	grep Found out.txt

quality:
	uvx ruff check --fix *.py
	uvx mypy --ignore-missing-imports *.py
