PY ?= python3
BUILD := report/.build

.PHONY: all install test analysis pdf
all: install test analysis pdf

install:
	$(PY) -m pip install -q -r requirements.txt

test:
	$(PY) -m pytest -q

analysis:
	PYTHONPATH=src $(PY) -m sgdfi

pdf:
	mkdir -p $(BUILD)
	pandoc report/portfolio_commentary.md -f markdown -t html5 -s --embed-resources \
		--resource-path=report --css=scripts/commentary.css \
		--metadata pagetitle="SGD Fixed Income Model Portfolio vs Benchmark" -o $(BUILD)/commentary.html
	NODE_PATH=$$(npm root -g) node scripts/build_pdf.cjs $(BUILD)/commentary.html report/portfolio_commentary.pdf 2
