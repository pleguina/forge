-- emp_payload for the trigger_demo algorithm
--
-- Input links 0-3 each carry one raw hit (data(31 downto 0), valid) into the
-- four hit decoders of the FORGE-generated algo_top; output link 0 carries
-- the trigger word. The port list is emp-fwk v0.9.3's
-- (components/payload/firmware/hdl/emp_payload.vhd).

library IEEE;
use IEEE.STD_LOGIC_1164.all;

use work.ipbus.all;
use work.emp_data_types.all;
use work.emp_project_decl.all;

use work.emp_device_decl.all;
use work.emp_ttc_decl.all;

use work.emp_slink_types.all;

entity emp_payload is
  port(
    clk         : in  std_logic;        -- ipbus signals
    rst         : in  std_logic;
    ipb_in      : in  ipb_wbus;
    ipb_out     : out ipb_rbus;
    clk40       : in  std_logic;
    clk_payload : in  std_logic_vector(2 downto 0);
    rst_payload : in  std_logic_vector(2 downto 0);
    clk_p       : in  std_logic;        -- data clock
    rst_loc     : in  std_logic_vector(N_REGION - 1 downto 0);
    clken_loc   : in  std_logic_vector(N_REGION - 1 downto 0);
    ctrs        : in  ttc_stuff_array;
    bc0         : out std_logic;
    d           : in  ldata(4 * N_REGION - 1 downto 0);  -- data in
    q           : out ldata(4 * N_REGION - 1 downto 0);  -- data out
    gpio        : out std_logic_vector(29 downto 0);  -- IO to mezzanine connector
    gpio_en     : out std_logic_vector(29 downto 0);  -- IO to mezzanine connector (three-state enables)
    slink_q : out slink_input_data_quad_array(SLINK_MAX_QUADS-1 downto 0);
    backpressure : in std_logic_vector(SLINK_MAX_QUADS-1 downto 0)
    );

end emp_payload;

architecture rtl of emp_payload is

  signal trigger_word  : std_logic_vector(31 downto 0);
  signal trigger_valid : std_logic;

begin

  algo : entity work.algo_top
    port map(
      ap_clk            => clk_p,
      ap_rst            => rst_loc(0),
      dec_0_raw_hit     => d(0).data(31 downto 0),
      dec_1_raw_hit     => d(1).data(31 downto 0),
      dec_2_raw_hit     => d(2).data(31 downto 0),
      dec_3_raw_hit     => d(3).data(31 downto 0),
      dec_0_raw_valid   => d(0).valid,
      dec_1_raw_valid   => d(1).valid,
      dec_2_raw_valid   => d(2).valid,
      dec_3_raw_valid   => d(3).valid,
      tout_trigger_word => trigger_word,
      tout_out_valid    => trigger_valid
      );

  -- One trigger word per event, so each valid frame is a one-frame packet.
  q(0).data           <= X"00000000" & trigger_word;
  q(0).valid          <= trigger_valid;
  q(0).start          <= trigger_valid;
  q(0).last           <= trigger_valid;
  q(0).start_of_orbit <= '0';
  q(0).strobe         <= '1';

  q(q'high downto 1) <= (others => LWORD_NULL);

  bc0 <= '0';

  ipb_out <= IPB_RBUS_NULL;

  gpio    <= (others => '0');
  gpio_en <= (others => '0');

  slink_q <= (others => SLINK_INPUT_DATA_ARRAY_NULL);

end rtl;
