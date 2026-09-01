# Consumes the package the way an embedder does, and through the CMake config
# the package ships -- so the test covers that file too.  Nothing here names a
# define or an include path by hand; if v8-embed::v8 does not carry them, the
# build fails, which is the point.
cmake_minimum_required(VERSION 3.20)
project(v8_embed_consumer CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

find_package(v8-embed CONFIG REQUIRED)
message(STATUS "v8-embed ${V8_EMBED_VERSION}: ${V8_EMBED_LIBRARY}")

add_executable(consumer consumer.cc)
target_link_libraries(consumer PRIVATE v8-embed::v8)

enable_testing()
add_test(NAME consumer COMMAND consumer)
