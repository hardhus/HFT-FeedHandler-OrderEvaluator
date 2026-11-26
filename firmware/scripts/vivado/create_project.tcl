# Regenerate the (board-less) Vivado project from the shared filelists: HFT_Core, no simulation sources
# (the tests are cocotb + Verilator).
# Usage: vivado -mode batch -source create_project.tcl -tclargs ?part? ?proj_dir?
set script_dir [file dirname [file normalize [info script]]]
set fw         [file normalize [file join $script_dir .. ..]]
set part       [expr {[llength $argv] > 0 ? [lindex $argv 0] : "xc7a100tcsg324-1"}]
set projdir    [file normalize [expr {[llength $argv] > 1 ? [lindex $argv 1] : [file join $fw .. build vivado $part]}]]
source [file join $script_dir read_f.tcl]

create_project hft $projdir -part $part -force
set_property target_language Verilog [current_project]

set srcs {}
foreach l {common.f xilinx.f core.f csr.f top.f} {
  set srcs [concat $srcs [read_f [file join $fw filelists $l]]]
}
add_files -norecurse $srcs
set_property file_type SystemVerilog [get_files -all *.sv]
# as synth_ooc.tcl: xilinx.f's wrappers need XPM
set_property verilog_define {HFT_HAVE_XPM} [current_fileset]
auto_detect_xpm

set_property top HFT_Core [get_filesets sources_1]
update_compile_order -fileset sources_1

puts "Project created: $projdir/hft.xpr (part $part)"
close_project
