# Ordinary project Makefile. FORGE must recognise this as a build file and
# leave it alone.
VIVADO ?= vivado
SRC := $(wildcard src/rtl/*.v) $(wildcard src/common/*.vhd)

.PHONY: all sim clean
all:
	$(VIVADO) -mode batch -source scripts/build.tcl

sim:
	xvlog $(SRC) sim/tb_regfile.v && xelab -R tb_regfile

clean:
	rm -rf .Xil vivado*.log vivado*.jou
