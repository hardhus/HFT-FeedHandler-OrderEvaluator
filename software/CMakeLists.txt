cmake_minimum_required(VERSION 3.15)
project(HFT_FeedHandler_OrderEvalutor CXX)

set(CMAKE_CXX_STANDART 17)
set(CMAKE_CXX_STANDART_REQUİRED ON)

find_package(Threads REQUIRED)

include_directories(include)

add_executable(hft_engine src/main.cpp)
target_link_libraries(hft_engine PRIVATE Threads::Threads)
