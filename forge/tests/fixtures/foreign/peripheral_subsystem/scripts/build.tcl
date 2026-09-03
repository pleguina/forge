# Vivado batch build. Not a FORGE artifact.
create_project -force periph ./build -part xc7a100tcsg324-1
add_files [glob src/rtl/*.v src/common/*.vhd]
add_files -fileset constrs_1 constr/pins.xdc
launch_runs impl_1 -to_step write_bitstream
