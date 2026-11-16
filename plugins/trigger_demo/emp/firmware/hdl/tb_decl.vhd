-- Testbench defaults; run_emp_sim.sh passes the file names and lengths as
-- generics of the testbench top.

library IEEE;
use IEEE.STD_LOGIC_1164.all;

package tb_decl is

  constant SOURCE_FILE : string := "source.txt";
  constant SINK_FILE   : string := "sink.txt";

  constant PLAYBACK_LENGTH      : natural := 64;
  constant CAPTURE_LENGTH       : natural := 64;
  constant WAIT_CYCLES_AT_START : natural := 0;
  constant PLAYBACK_OFFSET      : natural := 0;
  constant CAPTURE_OFFSET       : natural := 0;

  constant PLAYBACK_LOOP : boolean := false;
  constant STRIP_HEADER  : boolean := false;
  constant INSERT_HEADER : boolean := false;

end package;
