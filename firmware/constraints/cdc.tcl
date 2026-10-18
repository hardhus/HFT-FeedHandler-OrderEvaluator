# CDC constraints derived from attributes, not from instance names (audit 08 §2.9).
# Source after synthesis (or as an unmanaged Tcl constraint) once all clocks exist.
#  1) First flop of every ASYNC_REG chain whose driver is in another clock domain:
#     set_max_delay -datapath_only (min of the two periods); per synchronizer instance also
#     set_bus_skew, so the bits of a gray-coded bus arrive within one period of each other.
#  2) Cells marked (* hft_cdc_data = "TRUE" *) (data held stable by a handshake / FIFO
#     protocol): set_max_delay -datapath_only to every endpoint in another clock domain.

proc hft_cdc_period {clocks} {
  set p {}
  foreach c $clocks {
    lappend p [get_property PERIOD $c]
  }
  return [tcl::mathfunc::min {*}$p]
}

# Valid -from objects for set_max_delay / set_bus_skew: the clock pins of the source cells
# (a cell such as a LUTRAM is not accepted as a startpoint itself).
proc hft_cdc_from {cells} {
  return [get_pins -quiet -of_objects [get_cells -quiet $cells] -filter {IS_CLOCK}]
}

set hft_cdc_n_sync 0
set hft_cdc_n_data 0
set groups [dict create]

# ---- 1) synchronizer first stages ----
foreach ff [get_cells -quiet -hier -filter {IS_SEQUENTIAL && ASYNC_REG}] {
  set src_pins [all_fanin -quiet -flat -startpoints_only [get_pins -quiet $ff/D]]
  set src_cells [get_cells -quiet -of_objects $src_pins -filter {IS_SEQUENTIAL}]
  if {[llength [get_cells -quiet $src_cells -filter {ASYNC_REG}]]} {
    continue ;# later stage of a chain
  }
  set dst_clk [get_clocks -quiet -of_objects [get_pins -quiet $ff/C]]
  set src_clk [get_clocks -quiet -of_objects [get_pins -quiet -of_objects $src_cells -filter {IS_CLOCK}]]
  if {$dst_clk eq "" || $src_clk eq "" || $src_clk eq $dst_clk} {
    continue
  }
  set per [hft_cdc_period [concat $src_clk $dst_clk]]
  set_max_delay -datapath_only -from [hft_cdc_from $src_cells] -to $ff $per
  set key [file dirname [get_property NAME $ff]]
  dict lappend groups $key [list $src_cells $ff $per]
  incr hft_cdc_n_sync
}
dict for {key items} $groups {
  if {[llength $items] < 2} {
    continue ;# single bit: no skew between bits
  }
  set srcs {}
  set dsts {}
  set per 1e9
  foreach it $items {
    lappend srcs {*}[lindex $it 0]
    lappend dsts [lindex $it 1]
    set per [expr {min($per, [lindex $it 2])}]
  }
  set_bus_skew -from [hft_cdc_from $srcs] -to [get_cells $dsts] $per
}

# ---- 2) stable-data CDC paths ----
set hft_cdc_marked [get_cells -quiet -hier -filter {hft_cdc_data == TRUE}]
foreach src $hft_cdc_marked {
  set src_clk [get_clocks -quiet -of_objects [get_pins -quiet -of_objects $src -filter {IS_CLOCK}]]
  if {$src_clk eq ""} {
    continue
  }
  set ends [all_fanout -quiet -flat -endpoints_only [get_pins -quiet -of_objects $src -filter {DIRECTION == OUT}]]
  foreach ep [get_cells -quiet -of_objects $ends -filter {IS_SEQUENTIAL}] {
    set dst_clk [get_clocks -quiet -of_objects [get_pins -quiet $ep/C]]
    if {$dst_clk eq "" || $dst_clk eq $src_clk} {
      continue
    }
    set_max_delay -datapath_only -from [hft_cdc_from $src] -to $ep [hft_cdc_period [concat $src_clk $dst_clk]]
    incr hft_cdc_n_data
  }
}
# ---- 3) LUTRAM storage read in another clock domain (async FIFO memory) ----
# The hft_cdc_data attribute on a memory array does not survive mapping to LUTRAM primitives,
# so distributed RAM is recognised structurally: written on one clock, read asynchronously by
# flops on another. Safe only under a FIFO protocol (an entry is read only after its write
# pointer has crossed); report_cdc still lists these paths for review.
set hft_cdc_n_ram 0
set hft_cdc_n_unconstrained 0
# Primitive groups differ per family: DMEM on 7-series, CLB with subgroup LUTRAM on UltraScale(+).
foreach ram [get_cells -quiet -hier -filter {PRIMITIVE_GROUP == DMEM || PRIMITIVE_SUBGROUP == LUTRAM}] {
  set wclk [get_clocks -quiet -of_objects [get_pins -quiet -of_objects $ram -filter {IS_CLOCK}]]
  if {$wclk eq ""} {
    continue
  }
  set ends [all_fanout -quiet -flat -endpoints_only [get_pins -quiet -of_objects $ram -filter {DIRECTION == OUT}]]
  foreach ep [get_cells -quiet -of_objects $ends -filter {IS_SEQUENTIAL}] {
    set dst_clk [get_clocks -quiet -of_objects [get_pins -quiet $ep/C]]
    if {$dst_clk eq "" || $dst_clk eq $wclk} {
      continue
    }
    # LUTRAM macros (RAM32M...) are not valid startpoints; take the real ones (leaf RAM clock
    # pins) from the endpoint's fan-in, restricted to the write clock.
    set starts [all_fanin -quiet -flat -startpoints_only [get_pins -quiet $ep/D]]
    set from {}
    foreach sp $starts {
      if {[get_clocks -quiet -of_objects $sp] eq $wclk} {
        lappend from $sp
      }
    }
    if {![llength $from]} {
      puts "cdc.tcl: WARNING no write-clock startpoint found for [get_property NAME $ep]"
      incr hft_cdc_n_unconstrained
      continue
    }
    set per [hft_cdc_period [concat $wclk $dst_clk]]
    set_max_delay -datapath_only -from $from -to $ep $per
    incr hft_cdc_n_ram
  }
}
puts "cdc.tcl: $hft_cdc_n_sync synchronizer first stages; [llength $hft_cdc_marked] hft_cdc_data cells, $hft_cdc_n_data stable-data paths, $hft_cdc_n_ram LUTRAM read paths constrained, $hft_cdc_n_unconstrained unresolved"
