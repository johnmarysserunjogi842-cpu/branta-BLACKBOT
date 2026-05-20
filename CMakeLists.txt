# CMakeLists.txt
cmake_minimum_required(VERSION 3.16)

# ============================================================
# PROJECT
# ============================================================
project(market_core
        VERSION 1.0.0
        LANGUAGES CXX)

# ============================================================
# C++ STANDARD
# ============================================================
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

# Default to Release
if(NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE Release)
endif()

# ============================================================
# COMPILER OPTIMIZATION (INSTITUTION-GRADE)
# ============================================================
if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
    add_compile_options(
        -O3
        -fno-omit-frame-pointer
        -Wall
        -Wextra
        -Wpedantic
    )
endif()

# ============================================================
# PYTHON + PYBIND11 (REQUIRED — ALREADY USED IN CODE)
# ============================================================
find_package(Python3 REQUIRED COMPONENTS Interpreter Development.Module)
find_package(pybind11 REQUIRED)

# ============================================================
# SOURCES
# ============================================================
set(CORE_SOURCES
    core/data_fetcher.cpp
    core/trade_executor.cpp
)

# ============================================================
# BUILD PYTHON EXTENSION MODULE
# ============================================================
pybind11_add_module(market_core ${CORE_SOURCES})

# ============================================================
# LINKING
# ============================================================
target_link_libraries(market_core
    PRIVATE
        Python3::Module
)

# ============================================================
# OUTPUT LOCATION (DEPLOY.SH FRIENDLY)
# ============================================================
set_target_properties(market_core PROPERTIES
    LIBRARY_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}
    RUNTIME_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}
)

# ============================================================
# PLATFORM DEFINES
# ============================================================
if(UNIX)
    target_compile_definitions(market_core PRIVATE LINUX_BUILD)
endif()

# ============================================================
# SAFETY: NO PREFIX (ENSURE import market_core WORKS)
# ============================================================
set_target_properties(market_core PROPERTIES PREFIX "")
