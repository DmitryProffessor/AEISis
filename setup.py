from setuptools import setup, find_packages

setup(
    name="russtat",
    version="0.1",
    packages=find_packages(),  # найдёт папку russtat/
    # или явно:
    # package_dir={"": "russtat"},
    # py_modules=["src.russtat"],
)