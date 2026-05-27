#include <atomic>
#include <chrono>
#include <iostream>
#include <thread>
#include <vector>

#include "../include/hft_registers.hpp"

// Simulated PCIe BAR0 memory space (64-byte aligned for performance)
alignas(64) volatile uint32_t mock_pcie_bar[4] = {0};
std::atomic<bool> running{true};

// --- MOCK FPGA THREAD ---
// This function simulates the behavior of the FPGA hardware.
void mock_fpga_hw_behavior() {
    std::vector<std::pair<uint32_t, uint32_t>> market_feed = {
        {105, 200}, {102, 150}, {98, 500}, {95, 600}, {101, 100}};

    for (const auto &tick : market_feed) {
        if (!running) break;

        // Simulate network latency for packet ingestion
        std::this_thread::sleep_for(std::chrono::seconds(1));

        // Hardware-side write: Updating register values in BAR0
        mock_pcie_bar[REG_MARKET_PRICE] = tick.first;
        mock_pcie_bar[REG_MARKET_VOLUME] = tick.second;
        mock_pcie_bar[REG_DATA_VALID] = 1;

        // Hardware wait: Poll until software processes the data
        while (mock_pcie_bar[REG_DATA_VALID] == 1 && running) {
        }

        // Check if the software has issued a trade command
        if (mock_pcie_bar[REG_TRADE_EXECUTE] == 1) {
            std::cout << "[FPGA Hardware] >>> Order command received from Software. Executing UDP "
                         "trade packet... [SUCCESS]"
                      << std::endl;
        }
    }

    running = false;
}

// --- HFT DECISION ENGINE (C++) ---
// This simulates the High-Frequency Trading strategy engine using Kernel-Bypass/Polling.
void run_os_bypass_decision_core() {
    volatile uint32_t *pcie_ptr = mock_pcie_bar;

    std::cout << "[C++ OS-Bypass] Decision engine initialized. Polling BAR0 memory space..."
              << std::endl;

    while (running) {
        // Zero-Latency Polling: Continuously monitoring the VALID register
        if (pcie_ptr[REG_DATA_VALID] == 1) {
            uint32_t current_price = pcie_ptr[REG_MARKET_PRICE];
            uint32_t current_volume = pcie_ptr[REG_MARKET_VOLUME];

            std::cout << "[C++ OS-Bypass] New Market Tick -> Price: " << current_price
                      << " | Volume: " << current_volume << std::endl;

            if (current_price < 100) {
                std::cout << "[C++ OS-Bypass] Decision: \n";
                pcie_ptr[REG_TRADE_EXECUTE] = 1;
            } else {
                pcie_ptr[REG_TRADE_EXECUTE] = 0;
            }

            // Handshake: Clear VALID bit so hardware can process the next tick
            pcie_ptr[REG_DATA_VALID] = 0;
        }
    }
}

int main() {
    std::cout << "======================================================" << std::endl;
    std::cout << "   HFT ENGINE CO-DESIGN EXPERIMENT - RASPBERRY PI 5   " << std::endl;
    std::cout << "======================================================" << std::endl;

    // Start FPGA hardware emulation thread
    std::thread fpga_thread(mock_fpga_hw_behavior);

    // Start HFT engine on main thread
    run_os_bypass_decision_core();

    if (fpga_thread.joinable()) {
        fpga_thread.join();
    }

    std::cout << "===========================================================" << std::endl;
    std::cout << "[System] Test completed successfully. Shutting down engine." << std::endl;
    std::cout << "===========================================================" << std::endl;
    return 0;
}
