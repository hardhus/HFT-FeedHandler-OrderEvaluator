# Run the cdc_async_fifo equivalence TB in Vivado XSIM (non-project).
# Usage: vivado -mode batch -source xsim_run.tcl -tclargs <portable|xpm> ?seed?
# PASS/FAIL is decided by the TB's single "HFT_TB_RESULT:" line (exit codes of the
# Windows .bat wrappers are not reliable); exits 1 unless it says PASS.
set impl [lindex $argv 0]
set seed [expr {[llength $argv] > 1 ? [lindex $argv 1] : 1}]
if {$impl ni {portable xpm}} {
  puts "usage: -tclargs <portable|xpm> ?seed?"
  exit 2
}
set script_dir [file dirname [file normalize [info script]]]
set fw [file normalize [file join $script_dir .. ..]]
source [file join $script_dir read_f.tcl]

set out [file normalize [file join $fw .. build xsim $impl]]
file mkdir $out
cd $out

set srcs [concat [read_f $fw/filelists/common.f] [read_f $fw/filelists/xilinx.f] \
  [list [file join $fw tb sv tb_cdc_fifo_equiv.sv]]]
set defs [list -d HFT_HAVE_XPM]
if {$impl eq "xpm"} {
  lappend defs -d HFT_FIFO_IMPL_XPM
}

# On Windows call the unwrapped executables: the .bat wrappers split arguments at '='
# (e.g. -testplusarg seed=1). The vivado process already provides their environment.
proc tool {name} {
  set root $::env(XILINX_VIVADO)
  if {$::tcl_platform(platform) eq "windows"} {
    return [file join $root bin unwrapped win64.o $name.exe]
  }
  return [file join $root bin $name]
}

# Run a tool, echo its output, return the output (no Tcl error on stderr / exit code).
proc run {cmd} {
  catch {exec {*}$cmd 2>@1} out
  puts $out
  return $out
}

# glbl: global set/reset module required by the XPM simulation models.
lappend srcs [file join $::env(XILINX_VIVADO) data verilog src glbl.v]

run [list [tool xvlog] -sv {*}$defs {*}$srcs]
run [list [tool xelab] tb_cdc_fifo_equiv glbl -L xpm -timescale 1ns/1ps -s equiv]
set log [run [list [tool xsim] equiv -R -testplusarg seed=$seed]]

if {![regexp {HFT_TB_RESULT: PASS[^\n]*} $log result]} {
  puts "XSIM equivalence FAILED (impl=$impl seed=$seed)"
  exit 1
}
puts "XSIM equivalence: $result"
exit 0
