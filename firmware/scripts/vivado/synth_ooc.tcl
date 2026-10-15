# Out-of-context synthesis with CDC constraints, timing and CDC gates (non-project mode).
# Usage: vivado -mode batch -source synth_ooc.tcl -tclargs <top> <part> <profile> <arg...>
#   profile : net2host (net/wr/src side 156.25 MHz, host/rd/dst side 250 MHz) | host2net (swapped)
#   arg     : filelist names in firmware/filelists (e.g. common.f xilinx.f) or
#             HDL generics NAME:VALUE (e.g. DEPTH_LOG2:5 IMPL:xpm); ':' because Windows .bat
#             launchers split arguments at '='
# Reports: build/vivado/ooc/<top>_<profile>[_<generics>]/{utilization,timing_summary,cdc}.rpt
# Exits 1 if setup/hold slack is negative or report_cdc has Critical/Unsafe findings.
if {[llength $argv] < 4} {
  error "usage: -tclargs <top> <part> <net2host|host2net> <filelist|NAME:VALUE...>"
}
set top     [lindex $argv 0]
set part    [lindex $argv 1]
set profile [lindex $argv 2]
set lists   {}
set generics {}
foreach a [lrange $argv 3 end] {
  if {[string first : $a] >= 0} {
    lappend generics $a
  } else {
    lappend lists $a
  }
}
set script_dir [file dirname [file normalize [info script]]]
set fw [file normalize [file join $script_dir .. ..]]
set tag [join [concat $top $profile [string map {: ""} $generics]] _]
set out [file normalize [file join $fw .. build vivado ooc $tag]]
file mkdir $out
source [file join $script_dir read_f.tcl]

# Clock periods (ns) per profile. Legacy names kept for HFT_Top_Core.
set net 6.400
set host 4.000
switch $profile {
  net2host { set a $net; set b $host }
  host2net { set a $host; set b $net }
  default { error "profile must be net2host or host2net" }
}
set clock_periods [dict create clk $a wr_clk $a src_clk $a rd_clk $b dst_clk $b \
  clk_net $net net_clk $net clk_host $host host_clk $host clk_pcie 8.000]

# Synchronous ports -> clock (for I/O delays, so report_cdc knows every port's domain).
# Asynchronous inputs (sync_bit.d, reset_sync.rst_in) are deliberately left unclocked.
set port_clock {
  {^(wr_|din$|full$|overflow$)}        wr_clk
  {^(rd_|dout|empty$|underflow$)}      rd_clk
  {^src_}                              src_clk
  {^dst_}                              dst_clk
  {^(s_axis_|m_axis_|q$|rst_out$)}     clk
  {^(cfg_|ts_|stat_|m_udp_)}           clk
  {^rst$}                              {wr_clk clk}
}

set_part $part
foreach l $lists {
  set files [read_f [file join $fw filelists $l]]
  if {[llength $files]} {
    read_verilog -sv $files
  }
}
set_property verilog_define {HFT_HAVE_XPM} [current_fileset]
auto_detect_xpm
set gen_args {}
foreach g $generics {
  # Strings need quotes for Vivado (IMPL="xpm"); callers pass IMPL:xpm to avoid shell quoting.
  lassign [split $g :] gname gval
  if {![string is integer -strict $gval] && [string index $gval 0] ne "\""} {
    set gval "\"$gval\""
  }
  lappend gen_args -generic "$gname=$gval"
}
synth_design -top $top -part $part -mode out_of_context -verilog_define HFT_HAVE_XPM {*}$gen_args

dict for {p period} $clock_periods {
  if {[llength [get_ports -quiet $p]]} {
    create_clock -name $p -period $period [get_ports $p]
  }
}
foreach port [get_ports -quiet -filter {DIRECTION != INOUT}] {
  if {[llength [get_clocks -quiet $port]]} {
    continue ;# clock port
  }
  set name [get_property NAME $port]
  foreach {re clks} $port_clock {
    if {![regexp $re $name]} {
      continue
    }
    foreach c $clks {
      if {[llength [get_clocks -quiet $c]]} {
        if {[get_property DIRECTION $port] eq "IN"} {
          set_input_delay -clock $c 0 $port
        } else {
          set_output_delay -clock $c 0 $port
        }
        break
      }
    }
    break
  }
}

source [file join $fw constraints cdc.tcl]

report_utilization    -file [file join $out utilization.rpt]
report_timing_summary -file [file join $out timing_summary.rpt] -max_paths 20
report_cdc -details   -file [file join $out cdc.rpt]

# ---- gates ----
# Timing: register-to-register only. The I/O delays above exist so report_cdc knows each
# port's domain; with 0 ns they create artificial hold violations at the block boundary.
set fail {}
if {$hft_cdc_n_unconstrained > 0} {
  lappend fail "cdc.tcl: $hft_cdc_n_unconstrained CDC path(s) could not be constrained"
}
set r2r [list -from [all_registers -clock_pins] -to [all_registers -data_pins]]
set wns [get_property SLACK [get_timing_paths -quiet -max_paths 1 -setup {*}$r2r]]
set whs [get_property SLACK [get_timing_paths -quiet -max_paths 1 -hold {*}$r2r]]
if {$wns ne "" && $wns < 0} { lappend fail "setup WNS $wns" }
if {$whs ne "" && $whs < 0} { lappend fail "hold WHS $whs" }

# CDC: no Critical finding, and only expected warning IDs:
#   CDC-6  multi-bit synchronized with ASYNC_REG = gray-coded FIFO pointers; safe because
#          they change one bit at a time and cdc.tcl constrains their bus skew.
#   CDC-15 clock-enable controlled CDC = stable data captured with an enable (handshake data),
#          constrained by cdc.tcl.
# The summary table is parsed from cdc.rpt; if the report has neither a table nor the explicit
# no-crossing statement, the gate fails.
set allowed_warnings {CDC-6 CDC-15}
set cdc_summary {}
set fh [open [file join $out cdc.rpt]]
set cdc_text [read $fh]
close $fh
# Either the summary table or Vivado's explicit "no crossings" statement must be present.
if {![regexp {ID\s+Severity\s+Count\s+Description} $cdc_text]
    && ![regexp {All paths are Safely Timed\.} $cdc_text]} {
  lappend fail "cdc.rpt: neither a summary table nor 'All paths are Safely Timed'"
}
foreach line [split $cdc_text "\n"] {
  if {[regexp {^(CDC-\d+)\s+(Critical|Warning|Info)\s+(\d+)\s} $line -> id sev n]} {
    lappend cdc_summary "$id:$sev:$n"
    if {$sev eq "Critical" || ($sev eq "Warning" && $id ni $allowed_warnings)} {
      lappend fail "report_cdc $id $sev x$n"
    }
  }
}

set luts [llength [get_cells -quiet -hier -filter {PRIMITIVE_GROUP == LUT}]]
set ffs  [llength [get_cells -quiet -hier -filter {PRIMITIVE_GROUP == FLOP_LATCH}]]
set rams [llength [get_cells -quiet -hier -filter {PRIMITIVE_GROUP == DMEM}]]
set brams [llength [get_cells -quiet -hier -filter {PRIMITIVE_GROUP == BLOCKRAM}]]
puts "HFT_SYNTH: $tag LUT=$luts FF=$ffs LUTRAM=$rams BRAM=$brams WNS=$wns WHS=$whs CDC=[join $cdc_summary ,]"
if {[llength $fail]} {
  puts "HFT_SYNTH_RESULT: FAIL ($tag) [join $fail {; }] -> $out"
  exit 1
}
puts "HFT_SYNTH_RESULT: PASS ($tag) -> $out"
exit 0
