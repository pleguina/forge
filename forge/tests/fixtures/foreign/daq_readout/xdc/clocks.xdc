create_clock -period 8.000 -name sys_clk [get_ports sys_clk]
create_clock -period 4.000 -name link_clk [get_ports link_clk]
set_clock_groups -asynchronous -group [get_clocks sys_clk] -group [get_clocks link_clk]
