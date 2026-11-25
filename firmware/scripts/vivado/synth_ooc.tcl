# Out-of-context synthesis with CDC constraints, timing and CDC gates (non-project mode).
# Usage: vivado -mode batch -source synth_ooc.tcl -tclargs <top> <part> <profile> <arg...>
#   part    : FPGA part (e.g. xc7a100tcsg324-1, xcku5p-ffvb676-2-e); the reports and the result line
#             carry its name, so one top can be gated on several parts
#   profile : net2host (net/wr/src side 156.25 MHz, host/rd/dst side 250 MHz) | host2net (swapped)
#             | net2host125 (as net2host with a 125 MHz host: PCIe Gen2 x4 user clock, 128 bit)
#   arg     : filelist names in firmware/filelists (e.g. common.f xilinx.f), HDL generics
#             NAME:VALUE (e.g. DEPTH_LOG2:5 IMPL:xpm; ':' because Windows .bat launchers split
#             arguments at '='), or the flag "impl": also place and route the block out of context
#             (no board or pins needed) and gate on the routed timing instead of the synthesis
#             estimate (unplaced wires), which is then printed as SYNTH_WNS
# Reports: build/vivado/ooc/<top>_<profile>_<part>[_<generics>][_impl]/{utilization,timing_summary,cdc}.rpt (final),
#   with impl also {timing_summary,utilization,cdc}_<stage>.rpt as each stage (synth, place, route, physopt) ends and
#   synth.dcp, route.dcp
#   Log (justfile): build/vivado/synth_<top>_<profile>_<part>[_<generics>][_impl].log
# Exits 1 if setup/hold slack is negative, report_cdc has Critical/Unsafe findings or a ram_style memory was not
# inferred as RAM (Synth 8-7186).
if {[llength $argv] < 4} {
  error "usage: -tclargs <top> <part> <net2host|host2net|net2host125> <filelist|NAME:VALUE...>"
}
set top     [lindex $argv 0]
set part    [lindex $argv 1]
set profile [lindex $argv 2]
set lists   {}
set generics {}
set impl 0
foreach a [lrange $argv 3 end] {
  if {$a eq "impl"} {
    set impl 1
  } elseif {[string first : $a] >= 0} {
    lappend generics $a
  } else {
    lappend lists $a
  }
}
set script_dir [file dirname [file normalize [info script]]]
set fw [file normalize [file join $script_dir .. ..]]
set tag [join [concat $top $profile $part [string map {: ""} $generics] [expr {$impl ? "impl" : ""}]] _]
set out [file normalize [file join $fw .. build vivado ooc $tag]]
file mkdir $out
source [file join $script_dir read_f.tcl]

# Clock periods (ns) per profile. Legacy names kept for HFT_Top_Core.
set net 6.400
set host 4.000
switch $profile {
  net2host { set a $net; set b $host }
  host2net { set a $host; set b $net }
  net2host125 { set host 8.000; set a $net; set b $host }
  default { error "profile must be net2host, host2net or net2host125" }
}
set clock_periods [dict create clk $a wr_clk $a src_clk $a rd_clk $b dst_clk $b \
  clk_net $net net_clk $net clk_host $host host_clk $host clk_pcie 8.000]

# Synchronous ports -> clock (for I/O delays, so report_cdc knows every port's domain).
# Asynchronous inputs (sync_bit.d, reset_sync.rst_in) are deliberately left unclocked; any other
# port that no entry clocks fails the run (its crossings would be missing from report_cdc).
set async_ports {^(d|rst_in)$}
set port_clock {
  {^(wr_|din$|full$|overflow$)}        wr_clk
  {^(rd_|dout|empty$|underflow$)}      rd_clk
  {^src_}                              src_clk
  {^dst_}                              dst_clk
  {^(s_axis_|m_axis_|q$|rst_out$)}     {clk net_clk}
  {^(cfg_|ts_|stat_|cnt_|[sm]_udp_|[sm]_msg_|[sm]_pkt_|[sm]_ev_|m_res_|sym_|mpkt_|mres_|feed_)} {clk net_clk}
  {^(ctl_|cmd_|st_|ev_|ring_|loc_|ord_|[sm]_host_|[sm]_rej_|[sm]_sent_)} {clk net_clk}
  {^(s_axil_|host_|m_axi_|irq$)}      host_clk
  {^net_}                             net_clk
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
# Synth 8-7186: a memory with a ram_style that synthesis did not infer as RAM (F7: the order queue, an array of a
# packed struct, became about 8k flops). Gated: every memory with a ram_style must be inferred as asked.
set n_8_7186 [get_msg_config -id {Synth 8-7186} -count]

dict for {p period} $clock_periods {
  if {[llength [get_ports -quiet $p]]} {
    create_clock -name $p -period $period [get_ports $p]
  }
}
set unclocked {}
foreach port [get_ports -quiet -filter {DIRECTION != INOUT}] {
  if {[llength [get_clocks -quiet $port]]} {
    continue ;# clock port
  }
  set name [get_property NAME $port]
  set base [regsub {\[\d+\]$} $name {}] ;# bus bit din[3] -> din, so "din$" matches every bit
  set done 0
  foreach {re clks} $port_clock {
    if {![regexp $re $base]} {
      continue
    }
    foreach c $clks {
      if {[llength [get_clocks -quiet $c]]} {
        if {[get_property DIRECTION $port] eq "IN"} {
          set_input_delay -clock $c 0 $port
        } else {
          set_output_delay -clock $c 0 $port
        }
        set done 1
        break
      }
    }
    break
  }
  if {!$done && ![regexp $async_ports $base]} {
    lappend unclocked $base
  }
}

source [file join $fw constraints cdc.tcl]

set r2r [list -from [all_registers -clock_pins] -to [all_registers -data_pins]]
set synth_wns [get_property SLACK [get_timing_paths -quiet -max_paths 1 -setup {*}$r2r]]
# Stage reports: the full timing summary, utilization and CDC reports (and a checkpoint after synthesis and
# routing) are written as each stage ends, so a run that stops later (a tool crash in a long implementation) still
# leaves everything it had reached; an HFT_STAGE line in the log marks each stage.
proc hft_stage {name {dcp 0}} {
  global out tag
  set r2r [list -from [all_registers -clock_pins] -to [all_registers -data_pins]]
  set w [get_property SLACK [get_timing_paths -quiet -max_paths 1 -setup {*}$r2r]]
  set h [get_property SLACK [get_timing_paths -quiet -max_paths 1 -hold {*}$r2r]]
  report_timing_summary -file [file join $out timing_summary_$name.rpt] -max_paths 20
  report_utilization    -file [file join $out utilization_$name.rpt]
  report_cdc -details   -file [file join $out cdc_$name.rpt]
  if {$dcp} {
    write_checkpoint -force [file join $out $name.dcp]
  }
  puts "HFT_STAGE: $tag $name WNS=$w WHS=$h reports=$out/*_$name.rpt"
  return $w
}
if {$impl} {
  hft_stage synth 1
  # Out-of-context implementation: the constraints above (clocks, I/O delays, CDC) carry over.
  opt_design
  place_design
  hft_stage place
  route_design
  report_route_status -file [file join $out route_status.rpt]
  set route_wns [hft_stage route 1]
  # Post-route physical optimization when the routed design misses timing (A40, decision 44). After placement the
  # estimate was +0.501 ns and phys_opt_design did nothing; the CSR read path on the A7 at 125 MHz missed by
  # 0.100 ns only after routing (79 % routing delay). A design that meets timing is left as routed. It runs at any
  # negative WNS: F8 S2's A7 run at -0.530 ns gained 0.142 ns from it (a -0.5 ns cutoff had skipped it); a crash in
  # it still leaves the route stage's reports and route.dcp.
  if {$route_wns ne "" && $route_wns < 0} {
    phys_opt_design
    report_route_status -file [file join $out route_status.rpt]
    hft_stage physopt
  }
  set r2r [list -from [all_registers -clock_pins] -to [all_registers -data_pins]]
}

report_utilization    -file [file join $out utilization.rpt]
report_timing_summary -file [file join $out timing_summary.rpt] -max_paths 20
report_cdc -details   -file [file join $out cdc.rpt]

# ---- gates ----
# Timing: register-to-register only. The I/O delays above exist so report_cdc knows each
# port's domain; with 0 ns they create artificial hold violations at the block boundary.
set fail {}
if {[llength $unclocked]} {
  lappend fail "ports without a clock in port_clock: [lrange [lsort -unique $unclocked] 0 9]"
}
if {$hft_cdc_n_unconstrained > 0} {
  lappend fail "cdc.tcl: $hft_cdc_n_unconstrained CDC path(s) could not be constrained"
}
if {$impl} {
  set unrouted [llength [get_nets -quiet -hier -filter {ROUTE_STATUS == UNROUTED || ROUTE_STATUS == PARTIAL || ROUTE_STATUS == CONFLICTS}]]
  if {$unrouted > 0} { lappend fail "route_design left $unrouted net(s) unrouted" }
}
set wns [get_property SLACK [get_timing_paths -quiet -max_paths 1 -setup {*}$r2r]]
set whs [get_property SLACK [get_timing_paths -quiet -max_paths 1 -hold {*}$r2r]]
if {$n_8_7186 > 0} { lappend fail "Synth 8-7186 x$n_8_7186 (a ram_style memory not inferred as RAM)" }
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
#   CDC-26 LUTRAM read/write potential collision: allowed only on the endpoints cdc.tcl waived
#          (hft_cdc26_ends, decision 42). Waived paths should leave the report; if report_cdc still
#          lists them, every CDC-26 detail row must end at one of those endpoints.
set cdc26_rows 0
set cdc26_foreign {}
foreach line [split $cdc_text "\n"] {
  if {[regexp {^\s*\d+\s+CDC-26\s+\S+\s.*\s(\S+)/D\s*$} $line -> ep_name]} {
    incr cdc26_rows
    if {![dict exists $hft_cdc26_ends $ep_name]} {
      lappend cdc26_foreign $ep_name
    }
  }
}
foreach line [split $cdc_text "\n"] {
  if {[regexp {^(CDC-\d+)\s+(Critical|Warning|Info)\s+(\d+)\s} $line -> id sev n]} {
    set waived [expr {$id eq "CDC-26" && $sev eq "Warning" && $cdc26_rows == $n && ![llength $cdc26_foreign]}]
    lappend cdc_summary "$id:$sev:$n[expr {$waived ? {(waived)} : {}}]"
    if {$sev eq "Critical" || ($sev eq "Warning" && $id ni $allowed_warnings && !$waived)} {
      lappend fail "report_cdc $id $sev x$n[expr {$id eq "CDC-26" ? " (rows $cdc26_rows, not waived: [lrange $cdc26_foreign 0 4])" : ""}]"
    }
  }
}
lappend cdc_summary "CDC26_WAIVED_ENDS:[dict size $hft_cdc26_ends]"

# Primitive groups differ per family (7-series | UltraScale+): LUT | CLB.LUT, FLOP_LATCH | REGISTER,
# DMEM | CLB.LUTRAM; block RAMs by name (RAMB18/36 on both).
set luts [llength [get_cells -quiet -hier -filter {PRIMITIVE_GROUP == LUT || PRIMITIVE_SUBGROUP == LUT}]]
set ffs  [llength [get_cells -quiet -hier -filter {PRIMITIVE_GROUP == FLOP_LATCH || PRIMITIVE_GROUP == REGISTER}]]
set rams [llength [get_cells -quiet -hier -filter {PRIMITIVE_GROUP == DMEM || PRIMITIVE_SUBGROUP == LUTRAM}]]
set brams [llength [get_cells -quiet -hier -filter {IS_PRIMITIVE && REF_NAME =~ RAMB*}]]
set timing [expr {$impl ? "WNS=$wns WHS=$whs (routed) SYNTH_WNS=$synth_wns" : "WNS=$wns WHS=$whs"}]
puts "HFT_SYNTH: $tag LUT=$luts FF=$ffs LUTRAM=$rams BRAM=$brams $timing SYNTH_8_7186=$n_8_7186 CDC=[join $cdc_summary ,]"
# Worst register-to-register setup slack ending in each top-level block (report only: one run shows every block
# that misses, not just the worst path).
foreach blk [lsort [get_cells -quiet -filter {!IS_PRIMITIVE}]] {
  set ends [get_cells -quiet -hier -filter "NAME =~ $blk/* && IS_SEQUENTIAL"]
  if {![llength $ends]} continue
  set p [get_timing_paths -quiet -max_paths 1 -setup -from [all_registers -clock_pins] -to $ends]
  if {[llength $p]} {
    puts "HFT_SLACK: $tag $blk WNS=[get_property SLACK $p] LEVELS=[get_property LOGIC_LEVELS $p] FROM=[get_property STARTPOINT_PIN $p] TO=[get_property ENDPOINT_PIN $p]"
  }
}
if {[llength $fail]} {
  puts "HFT_SYNTH_RESULT: FAIL ($tag) [join $fail {; }] -> $out"
  exit 1
}
puts "HFT_SYNTH_RESULT: PASS ($tag) -> $out"
exit 0
