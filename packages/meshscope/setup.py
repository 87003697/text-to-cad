"""Build the optional hierarchical SAT extension."""

from __future__ import annotations

import os

from setuptools import Extension, setup


if os.name == "nt":
    compile_args = ["/O2", "/std:c++17"]
else:
    compile_args = ["-O3", "-std=c++17"]

setup(
    ext_modules=[
        Extension(
            "meshscope.voxblame._native",
            ["src/meshscope/voxblame/_native.cpp"],
            language="c++",
            extra_compile_args=compile_args,
        )
    ]
)
