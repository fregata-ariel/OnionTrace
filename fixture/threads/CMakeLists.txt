cmake_minimum_required(VERSION 3.20)
project(thread_fixture C)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)
find_package(Threads REQUIRED)
add_library(thread_helpers STATIC helpers.c)
add_executable(nr-softmodem gnb.c)
add_executable(nr-uesoftmodem ue.c)
target_link_libraries(nr-softmodem PRIVATE thread_helpers Threads::Threads)
target_link_libraries(nr-uesoftmodem PRIVATE thread_helpers Threads::Threads)
# The same external definition compiled under incompatible conditions must stay
# ambiguous if both variants are selected; it is not a unique cross-TU target.
add_library(variant_a OBJECT variant.c)
add_library(variant_b OBJECT variant.c)
target_compile_definitions(variant_b PRIVATE VARIANT_B=1)
add_executable(ambiguous ambiguous.c $<TARGET_OBJECTS:variant_a> $<TARGET_OBJECTS:variant_b>)
add_executable(recursive recursive.c)
# This intentionally models an ambiguous link input. Extraction needs configure,
# syntax validation and File API membership, not a successfully linked binary.
