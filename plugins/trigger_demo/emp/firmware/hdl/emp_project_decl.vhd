-- emp_project_decl for simulation on emp-fwk's testbench board
--
-- emp-fwk v0.9.3 projects/examples/testbench, with region 0 (links 0-3)
-- buffered in and out.

library IEEE;
use IEEE.STD_LOGIC_1164.all;

use work.emp_framework_decl.all;
use work.emp_device_types.all;

-------------------------------------------------------------------------------
package emp_project_decl is

  constant PAYLOAD_REV : std_logic_vector(31 downto 0) := X"00000001";

  constant LB_ADDR_WIDTH   : integer             := 10;
  constant CLOCK_RATIO     : integer             := 6;
  constant CLOCK_COMMON_RATIO : integer          := 24;
  constant CLOCK_AUX_DIV : clock_divisor_array_t := (12, 6, 4);  -- Dividers of CLOCK_COMMON_RATIO * 40 MHz

  constant PAYLOAD_LATENCY : integer := 5;

  constant REGION_CONF : region_conf_array_t := (
    0      => (no_mgt, buf, no_fmt, buf, no_mgt),
    others => kDummyRegion
    );

end emp_project_decl;
-------------------------------------------------------------------------------
