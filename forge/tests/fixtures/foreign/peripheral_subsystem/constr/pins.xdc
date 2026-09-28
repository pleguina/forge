create_clock -period 10.000 -name pclk [get_ports pclk]
set_property PACKAGE_PIN W5 [get_ports pclk]
set_property IOSTANDARD LVCMOS33 [get_ports presetn]
