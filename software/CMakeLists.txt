# C++ host side (F8 S6, audit/15 §2.7): hft::Device, the Verilator co-simulation device (SimDevice) and its smoke
# test. `just cosim` generates the vectors, configures, builds and runs ctest.
cmake_minimum_required(VERSION 3.20)
project(hft_software LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

# Found from PATH (OSS CAD Suite's bin/../share/verilator), no VERILATOR_ROOT needed (audit/15 S0). Its config sets
# the VERILATOR_ROOT variable, which find_package may use as a search root (CMP0144).
if(POLICY CMP0144)
  cmake_policy(SET CMP0144 NEW)
endif()
find_package(verilator REQUIRED HINTS $ENV{VERILATOR_ROOT})

set(FW ${CMAKE_CURRENT_SOURCE_DIR}/../firmware)
set(HFT_VECTORS "${CMAKE_BINARY_DIR}/../smoke.vec" CACHE FILEPATH "co-simulation vector file")

# The sources of a .f filelist (paths relative to the list; '#' and '//' lines skipped), as firmware/tb's runner and
# the Vivado scripts read them.
function(hft_read_filelist out list)
  set(f ${FW}/filelists/${list})
  get_filename_component(dir ${f} DIRECTORY)
  file(STRINGS ${f} lines)
  set(srcs ${${out}})
  foreach(l IN LISTS lines)
    string(STRIP "${l}" l)
    if(l STREQUAL "" OR l MATCHES "^#" OR l MATCHES "^//")
      continue()
    endif()
    get_filename_component(p ${dir}/${l} ABSOLUTE)
    list(APPEND srcs ${p})
  endforeach()
  set(${out} ${srcs} PARENT_SCOPE)
  set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS ${f})
endfunction()

# The cocotb system tests' build: the RTL lists, then the assertion modules and binds (common.runner SVA_FOR), so the
# assertions fire here too; the waiver file first (generated RTL).
set(SV_SOURCES ${FW}/lint/waivers.vlt)
foreach(list common.f core.f csr.f top.f sva.f sva_core.f sva_csr.f)
  hft_read_filelist(SV_SOURCES ${list})
endforeach()

add_library(hft_sim STATIC sim/sim_device.cpp)
target_include_directories(hft_sim PUBLIC include sim)
# Warnings for this project's sources only; Verilator's headers as system headers (its own code warns).
target_include_directories(hft_sim SYSTEM PRIVATE ${VERILATOR_ROOT}/include ${VERILATOR_ROOT}/include/vltstd)
set_source_files_properties(sim/sim_device.cpp PROPERTIES COMPILE_OPTIONS "-Wall;-Wextra;-Werror")
verilate(hft_sim
  PREFIX Vhft_core
  TOP_MODULE HFT_Core
  SOURCES ${SV_SOURCES}
  # The cocotb builds' flags (common.runner) plus the --public-flat-rw cocotb's Verilator runner adds: the same
  # model, and the assertion modules' unused bind inputs stay public (no UNUSEDSIGNAL), as there.
  VERILATOR_ARGS -Wall --assert --x-assign unique --x-initial unique --public-flat-rw)

add_executable(hft_smoke sim/tests/smoke_test.cpp)
target_link_libraries(hft_smoke PRIVATE hft_sim)
target_compile_options(hft_smoke PRIVATE -Wall -Wextra -Werror)

enable_testing()
add_test(NAME cosim_smoke COMMAND hft_smoke ${HFT_VECTORS})
add_test(NAME cosim_smoke_seed2 COMMAND hft_smoke ${HFT_VECTORS} 2)
