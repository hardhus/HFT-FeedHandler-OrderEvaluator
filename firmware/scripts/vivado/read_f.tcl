# Shared helper: parse a .f filelist (Verilator -F semantics: paths are
# relative to the .f file itself; blank lines and '#'/'//' comments ignored).
proc read_f {f} {
  set dir [file dirname [file normalize $f]]
  set out {}
  set fh [open $f]
  set lines [split [read $fh] "\n"]
  close $fh
  foreach l $lines {
    set l [string trim $l]
    if {$l eq "" || [string index $l 0] eq "#" || [string range $l 0 1] eq "//"} continue
    lappend out [file normalize [file join $dir $l]]
  }
  return $out
}
