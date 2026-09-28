create_clock -period 4.000 -name clk [get_ports clk]
set_property IOSTANDARD LVCMOS18 [get_ports rst_n]
