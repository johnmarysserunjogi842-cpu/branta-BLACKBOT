from setuptools import setup, Extension
import pybind11

ext_modules = [
    Extension(
        "core.trade_executor",
        ["core/trade_executor_bindings.cpp"],
        include_dirs=[pybind11.get_include()],
        language="c++",
    ),
]

setup(
    name="trade_executor",
    ext_modules=ext_modules,
)
