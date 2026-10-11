# Out-of-context synthesis for timing/utilization reports (non-project mode).
# Usage: vivado -mode batch -source synth_ooc.tcl -tclargs <top> <part> <filelist...>
#   filelist names are relative to firmware/filelists, e.g. "legacy.f common.f"
if {[llength $argv] < 3} {
  error "usage: -tclargs <top> <part> <filelist...>"
}
set top        [lindex $argv 0]
set part       [lindex $argv 1]
set lists      [lrange $argv 2 end]
set script_dir [file dirname [file normalize [info script]]]
set fw         [file normalize [file join $script_dir .. ..]]
set out        [file normalize [file join $fw .. build vivado ooc $top]]
file mkdir $out
source [file join $script_dir read_f.tcl]

set_part $part
foreach l $lists {
  set files [read_f [file join $fw filelists $l]]
  if {[llength $files]} { read_verilog -sv $files }
}
auto_detect_xpm
synth_design -top $top -part $part -mode out_of_context

# net 156.25 MHz, host 250 MHz (legacy clk_pcie: 125 MHz), generic clk 156.25 MHz
foreach {p period} {clk 6.400 clk_net 6.400 net_clk 6.400 clk_pcie 8.000 clk_host 4.000 host_clk 4.000} {
  if {[llength [get_ports -quiet $p]]} {
    create_clock -name $p -period $period [get_ports $p]
  }
}

report_utilization    -file [file join $out utilization.rpt]
report_timing_summary -file [file join $out timing_summary.rpt] -max_paths 20
puts "Reports written to $out"
