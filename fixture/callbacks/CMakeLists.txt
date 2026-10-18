cmake_minimum_required(VERSION 3.20)
project(callback_fixture C)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)
# Extraction-only fixtures: external queue functions deliberately have no body.
add_executable(nr-softmodem main.c runtime.c submit.c)
add_executable(nr-uesoftmodem other.c runtime.c)
add_executable(incompatible incompatible.c)
