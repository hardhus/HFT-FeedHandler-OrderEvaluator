# Regenerate the (board-less) Vivado project from the shared filelists.
# Usage: vivado -mode batch -source create_project.tcl -tclargs ?part? ?proj_dir?
set script_dir [file dirname [file normalize [info script]]]
set fw         [file normalize [file join $script_dir .. ..]]
set part       [expr {[llength $argv] > 0 ? [lindex $argv 0] : "xc7a100tcsg324-1"}]
set projdir    [file normalize [expr {[llength $argv] > 1 ? [lindex $argv 1] : [file join $fw .. build vivado $part]}]]
source [file join $script_dir read_f.tcl]

create_project hft $projdir -part $part -force
set_property target_language Verilog [current_project]

set srcs [concat [read_f $fw/filelists/legacy.f] [read_f $fw/filelists/common.f]]
add_files -norecurse $srcs
add_files -fileset sim_1 -norecurse [glob $fw/tb/legacy/*.sv]
set_property file_type SystemVerilog [get_files -all *.sv]

set_property top HFT_Top_Core [get_filesets sources_1]
set_property top tb_hft_top_torture [get_filesets sim_1]
set_property top_lib xil_defaultlib [get_filesets sim_1]
update_compile_order -fileset sources_1
update_compile_order -fileset sim_1

puts "Project created: $projdir/hft.xpr (part $part)"
close_project
