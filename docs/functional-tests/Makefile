TEX := main.tex
OUT := build
PDF := $(OUT)/grid-trading-functional-tests.pdf
DELIVERY_DIR := ../../output/pdf
DELIVERY_PDF := $(DELIVERY_DIR)/grid-trading-functional-tests.pdf

.PHONY: all clean

all: $(DELIVERY_PDF)

$(PDF): $(TEX) $(wildcard sections/*.tex)
	latexmk -xelatex -interaction=nonstopmode -halt-on-error -outdir=$(OUT) -jobname=grid-trading-functional-tests $(TEX)

$(DELIVERY_PDF): $(PDF)
	mkdir -p $(DELIVERY_DIR)
	cp $(PDF) $(DELIVERY_PDF)

clean:
	latexmk -C -outdir=$(OUT) -jobname=grid-trading-functional-tests $(TEX)
