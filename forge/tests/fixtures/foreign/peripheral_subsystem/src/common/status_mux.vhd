-- Status aggregation, written in VHDL. Both languages must land in one
-- module registry.
library ieee;
use ieee.std_logic_1164.all;

entity status_mux is
  port (
    pclk              : in  std_logic;
    presetn           : in  std_logic;
    status_word       : in  std_logic_vector(31 downto 0);
    status_word_valid : in  std_logic;
    host_status       : out std_logic_vector(31 downto 0);
    host_status_valid : out std_logic
  );
end entity;

architecture rtl of status_mux is
begin
  process (pclk)
  begin
    if rising_edge(pclk) then
      if presetn = '0' then
        host_status       <= (others => '0');
        host_status_valid <= '0';
      else
        host_status       <= status_word;
        host_status_valid <= status_word_valid;
      end if;
    end if;
  end process;
end architecture;
