from setuptools import setup, find_packages

setup(
    name="spiceEV",
    version="0.0.0",
    description="Simulation Program for Individual Charging Events of Electric Vehicles.",
    url="https://github.com/rl-institut/spice_ev",
    author="Reiner Lemoine Institut",
    # author_email='sabine.haas@rl-institut.de',
    license="MIT",
    packages= find_packages(),
    package_data={},
    #long_description=read("README.rst"),
    #long_description_content_type="text/x-rst",
    #python_requires=">=3.5, <4",
    #install_requires=[],
    #extras_require={},
)