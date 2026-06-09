# HFT FeedHandler & OrderEvaluator

This repository contains a high-frequency trading (HFT) hardware-accelerated feed handler and order evaluation engine designed for ultra-low latency UDP network packet processing. It features a hardware/software co-design approach leveraging FPGA RTL execution and host software control.

## 📂 Repository Structure

* **`/firmware/rtl`**: SystemVerilog hardware source files containing the UDP parser, asynchronous FIFO wrappers, register banks, and top-level synthesis logic (`HFT_Top.sv`).
* **`/firmware/tb`**: Hardware testbenches for individual module and system-level validation.
* **`/firmware/vivado`**: AMD Xilinx Vivado simulation models and project binaries.
* **`/software/include`**: C++ header structures mapping memory-mapped registers (`hft_registers.hpp`) between hardware and software layers.
* **`/software/src`**: C++ implementation files managing execution control and interface drivers (`main.cpp`).

## 🛠️ Stack & Technologies

* **Hardware (RTL):** `SystemVerilog`
* **Software:** `C++ (C++17/C++20)`
* **EDA Toolchain:** `AMD Xilinx Vivado` / `XSIM`
* **Protocols:** `UDP/IP`, `AXI-Stream (AXIS)`, `AXI-Lite (AXIL)`

## 🚧 Project Status

This project is currently under **active development** (WIP). Basic packet parsing structures and network interface wrappers have been instantiated; full system-level deployment and performance optimization benchmarks are pending execution.
